# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import os
import shutil
import sqlite3

from qgis.core import (
    Qgis,
    QgsDataProvider,
    QgsFields,
    QgsProject,
    QgsRasterLayer,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import output_paths, security
from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from ..core.save_guard import temp_folder_ready
from ..core.snapshot_files import sqlite_read_only_uri
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _find_layer


_RASTER_CREATION = ["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"]

_RASTER_SIDECARS = (".aux.xml", ".ovr")


def register_persist_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="make_layers_permanent",




        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Make the temporary layers permanent[ in {gpkg_path}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layers": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                "gpkg_path": {"type": "string"},
                "save": {"type": "boolean"},
            },
            "required": [],
        },
        handler=_make_layers_permanent,
    ))


def _origins(*names: str) -> set:

    found = set()
    for name in names:
        value = enum_member(Qgis, "FieldOrigin", name, None)
        if value is None:
            value = getattr(QgsFields, f"Origin{name}", None)
        if value is not None:
            found.add(value)
    return found


def _stored_fields(layer) -> list[int]:


    kept = _origins("Provider", "Edit", "Unknown")
    fields = layer.fields()
    return [index for index in range(fields.count()) if fields.fieldOrigin(index) in kept]


def _gpkg_tables(path: str) -> set[str] | None:









    if not os.path.exists(path):
        return set()
    try:
        connection = sqlite3.connect(sqlite_read_only_uri(path), timeout=1.0, uri=True)
        try:
            rows = connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        finally:
            connection.close()
        return {str(name).casefold() for (name,) in rows}
    except (OSError, sqlite3.Error) as exc:
        log_warning(f"make_layers_permanent: the tables of {path} cannot be listed: {exc}")
        return None


def _fresh_gpkg(path: str) -> str:

    stem, number = os.path.splitext(path)[0], 2
    while True:
        candidate = f"{stem}_{number}.gpkg"
        if not os.path.exists(candidate):
            return candidate if security.fits_path(candidate, len("-journal")) else ""
        number += 1


def _unique(stem: str, taken: set[str]) -> str:
    name, number = stem, 2
    while name.casefold() in taken:
        name, number = f"{stem}_{number}", number + 1
    taken.add(name.casefold())
    return name


def _target(args: dict, project) -> tuple[str, dict | None]:

    from ..core import policy
    from ._layers import _source_key
    from .layer_tools import _default_gpkg_path

    asked = str(args.get("gpkg_path") or "").strip()
    if asked:
        path = security.expand_path(asked)
        if os.path.isdir(path):
            stem = os.path.splitext(os.path.basename(project.fileName() or ""))[0] or "layers"
            path = os.path.join(path, f"{output_paths.safe_file_name(stem, 'layers')}_data.gpkg")
        elif os.path.splitext(path)[1].lower() != ".gpkg":
            return "", tool_error(f"{os.path.basename(path)} is not a GeoPackage name.", "INVALID_ARGS",
                                  hint="persist_gpkg_name", name=os.path.basename(path))
    else:
        path = _default_gpkg_path("layers" if not project.fileName() else "")
    if _source_key(path).startswith(_source_key(policy.AGENT_TMP_DIR).rstrip("/") + "/"):
        return "", tool_error("gpkg_path is inside the agent's scratch folder, which is pruned.", "INVALID_ARGS",
                              hint="persist_gpkg_in_scratch")
    error = security.validate_path(path, write=True)
    if error:
        return "", tool_error(error, "PERMISSION_DENIED", hint="persist_gpkg_path_refused")
    if not security.fits_path(path, len("-journal")):
        return "", tool_error(f"{path} is longer than Windows opens.", "INVALID_ARGS",
                              hint="persist_path_too_long")
    return path, None


def _write_vector(layer, gpkg: str, table: str) -> str:

    project = QgsProject.instance()

    def write(layer_options: list[str]) -> tuple:
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = table
        options.fileEncoding = "UTF-8"
        options.attributes = _stored_fields(layer)
        options.layerOptions = layer_options
        options.saveMetadata = True
        options.layerMetadata = layer.metadata()
        if os.path.exists(gpkg):
            options.actionOnExistingFile = enum_member(
                QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteLayer")
        return QgsVectorFileWriter.writeAsVectorFormatV3(layer, gpkg, project.transformContext(), options)

    no_error = enum_member(QgsVectorFileWriter, "WriterError", "NoError")
    result = write([])
    if result[0] != no_error and any(f.name().casefold() == "fid" for f in layer.fields()):

        result = write(["FID=gpkg_fid"])
    if result[0] != no_error:
        return str(result[1] if len(result) > 1 and result[1] else result[0])
    return ""


def _save_style_inside(layer) -> bool:




    try:
        saver = getattr(layer, "saveStyleToDatabaseV2", None) or layer.saveStyleToDatabase
        saver(layer.name(), "", True, "")
        listed = layer.listStylesInDatabase()
        names = listed[2] if isinstance(listed, tuple) and len(listed) > 2 else []
        return layer.name() in list(names or [])
    except Exception as exc:  # noqa: BLE001
        log_warning(f"make_layers_permanent: style of {layer.name()} not stored in the GeoPackage: {exc}")
        return False


def _repoint(layer, uri: str, provider: str) -> None:
    options = QgsDataProvider.ProviderOptions()
    options.transformContext = QgsProject.instance().transformContext()

    metadata = layer.metadata()
    layer.setDataSource(uri, layer.name(), provider, options)
    layer.setMetadata(metadata)
    layer.triggerRepaint()


def _vector(layer, was: str, gpkg: str, taken: set[str]) -> dict:
    name = layer.name()
    if layer.isEditable():
        return {"name": name, "skipped": "it is being edited: save or discard the edits first"}



    subset = layer.subsetString() or ""
    if subset and not layer.setSubsetString(""):
        return {"name": name, "skipped": "its filter could not be lifted to copy every feature, so the layer "
                                         "was left as it was"}
    try:
        expected = layer.featureCount()
        table = _unique(output_paths.safe_file_name(name, "layer").replace(" ", "_"), taken)
        error = _write_vector(layer, gpkg, table)
    finally:
        if subset:
            layer.setSubsetString(subset)
    if error:
        return {"name": name, "skipped": f"the GeoPackage write failed: {error[:200]}"}
    uri = f"{gpkg}|layername={table}"
    probe = QgsVectorLayer(uri, table, "ogr")
    written = probe.featureCount() if probe.isValid() else -1
    probe = None
    if expected >= 0 and written != expected:

        return {"name": name, "skipped": f"the copy holds {max(written, 0)} of {expected} features, so the layer "
                                         "was left as it was"}
    _repoint(layer, uri, "ogr")
    if not layer.isValid():
        return {"name": name, "error": f"the layer does not open from {uri} after the move"}
    entry = {"name": name, "was": was, "table": table, "features": expected}
    if subset:



        shown = layer.featureCount() if layer.setSubsetString(subset) else -1
        if shown >= 0:
            entry["filter"] = subset
            entry["features_shown"] = shown
        else:
            layer.setSubsetString("")
            entry["filter_not_restored"] = subset
            entry["note"] = "Every feature was kept, but the filter does not read on the GeoPackage."
            entry["note_hint"] = "persist_filter_not_restored"
    entry["style_in_gpkg"] = _save_style_inside(layer)
    return entry


def _free_tif(folder: str, stem: str, taken: set[str]) -> str:

    while True:
        target = os.path.join(folder, f"{_unique(stem, taken)}.tif")
        if not any(os.path.exists(target + extra) for extra in ("",) + _RASTER_SIDECARS):
            return target


def _raster(layer, gpkg: str, taken: set[str]) -> dict:
    name = layer.name()
    if layer.providerType() != "gdal":
        return {"name": name, "skipped": f"a {layer.providerType()} raster is not a file to copy"}
    source = str(layer.source() or "").split("|", 1)[0]
    if not os.path.isfile(source):
        return {"name": name, "skipped": "its scratch file is gone"}
    folder = os.path.dirname(gpkg)
    stem = output_paths.safe_file_name(f"{os.path.splitext(os.path.basename(gpkg))[0]}_{name}", "raster")
    target = _free_tif(folder, stem, taken)
    if not security.fits_path(target, len(".aux.xml")):

        target = _free_tif(folder, "raster", taken)
        if not security.fits_path(target, len(".aux.xml")):
            return {"name": name, "skipped": f"{folder} is too deep for a file name Windows opens"}
    try:
        if os.path.splitext(source)[1].lower() in (".tif", ".tiff"):
            shutil.copyfile(source, target)
            for sidecar in _RASTER_SIDECARS:
                if os.path.isfile(source + sidecar):
                    shutil.copyfile(source + sidecar, target + sidecar)
        else:
            from osgeo import gdal

            gdal.UseExceptions()
            gdal.Translate(target, source, format="GTiff", creationOptions=_RASTER_CREATION)
    except (OSError, RuntimeError) as exc:
        return {"name": name, "skipped": f"the copy failed: {str(exc)[:200]}"}
    probe = QgsRasterLayer(target, name, "gdal")
    valid = probe.isValid()
    probe = None
    if not valid:
        return {"name": name, "skipped": f"the copy at {target} does not open"}
    _repoint(layer, target, "gdal")
    if not layer.isValid():
        return {"name": name, "error": f"the layer does not open from {target} after the move"}
    return {"name": name, "was": "scratch", "file": target}


def _make_layers_permanent(args: dict) -> dict:
    from .layer_tools import scratch_layers

    project = QgsProject.instance()
    memory, temporary = scratch_layers(project)
    kinds = {layer.id(): "memory" for layer in memory}
    kinds.update({layer.id(): "scratch" for layer in temporary})
    skipped: list[dict] = []
    wanted = args.get("layers")
    if isinstance(wanted, list) and wanted:
        chosen = []
        for ref in wanted:
            layer = _find_layer(str(ref))
            if layer is None:
                skipped.append({"name": str(ref), "skipped": "no layer by that name"})
            elif layer.id() in _WRITING:
                skipped.append({"name": layer.name(), "skipped": "it is being written to a GeoPackage now"})
            elif layer.id() not in kinds:
                skipped.append({"name": layer.name(), "skipped": "already stored in a lasting file"})
            elif layer not in chosen:
                chosen.append(layer)
    else:
        chosen = memory + temporary
    if not chosen:
        out = {"made_permanent": [], "message": "No memory or scratch layer to move: every layer already reads "
                                                "a lasting file."}
        if skipped:
            out["skipped"] = skipped
        return out

    gpkg, refused = _target(args, project)
    if refused:
        return refused
    os.makedirs(os.path.dirname(gpkg), exist_ok=True)
    taken = _gpkg_tables(gpkg)
    moved_from = ""
    if taken is None:


        fresh = _fresh_gpkg(gpkg)
        if not fresh:
            return tool_error(f"The tables of {gpkg} cannot be read, so nothing was written into it.",
                              "INVALID_ARGS", hint="persist_gpkg_unreadable", path=gpkg)
        moved_from, gpkg, taken = gpkg, fresh, set()
    taken |= _reserved_tables(gpkg)
    files_taken: set[str] = set()
    moved: list[dict] = []
    for layer in chosen:
        try:
            if isinstance(layer, QgsVectorLayer):
                entry = _vector(layer, kinds[layer.id()], gpkg, taken)
            elif isinstance(layer, QgsRasterLayer):
                entry = _raster(layer, gpkg, files_taken)
            else:
                entry = {"name": layer.name(), "skipped": "only vector and raster layers are moved"}
        except Exception as exc:  # noqa: BLE001
            log_warning(f"make_layers_permanent: {layer.name()}: {exc}")
            entry = {"name": layer.name(), "error": str(exc)[:200]}
        (moved if "was" in entry else skipped).append(entry)

    out: dict = {"gpkg_path": gpkg, "made_permanent": moved}
    if moved_from:
        out["gpkg_note"] = (f"The tables of {moved_from} could not be read (another program may hold it), so the "
                            f"layers went to a new file and nothing in {os.path.basename(moved_from)} was touched.")
    if skipped:
        out["skipped"] = skipped
    if moved and args.get("save", True) is not False:
        if project.fileName():
            temp_folder_ready("agent")
            if project.write():
                out["project_saved"] = project.fileName()
            else:
                out["project_note"] = ("The layers read the new files; the project needs a write. "
                                       "save_project writes it.")
        else:
            out["project_note"] = ("The project has never been saved; the layers read the new files, and "
                                   "save_project writes the project.")
    return out



















_COPY_GROUP = 5000

_IDLE_WAIT_MS = 2000
_IDLE_TRIES = 900



_WRITING: dict[str, tuple[str, str]] = {}


def writing() -> dict:

    return dict(_WRITING)


def _reserved_tables(gpkg: str) -> set[str]:
    key = os.path.normcase(os.path.abspath(gpkg))
    return {table.casefold() for path, table in list(_WRITING.values())
            if os.path.normcase(os.path.abspath(path)) == key}


def _release(items) -> None:
    for item in items:
        _WRITING.pop(item["id"], None)


def save_run_layers(layer_ids, busy=None) -> int:


    project = QgsProject.instance()
    layers = [project.mapLayer(str(layer_id)) for layer_id in dict.fromkeys(layer_ids or ())]
    layers = [layer for layer in layers if isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory"]
    return len(store_in_background(layers, busy).get("layers") or ())


def _run_open() -> bool:
    from ..core import layer_order

    return layer_order.current_run() is not None


def store_in_background(layers, busy=None, save: bool = True) -> dict:







    from qgis.core import QgsVectorLayerFeatureSource

    from ..core.background import start_kept_thread

    project = QgsProject.instance()
    layers = [layer for layer in layers if isinstance(layer, QgsVectorLayer) and layer.isValid()
              and not layer.isEditable() and layer.id() not in _WRITING]
    if not layers:
        return {}
    gpkg, refused = _target({}, project)
    if refused:
        log_warning(f"Layers not written to a GeoPackage: {refused.get('_error') or refused}")
        return {}
    taken = _gpkg_tables(gpkg)
    if taken is None:
        gpkg, taken = _fresh_gpkg(gpkg), set()
        if not gpkg:
            return {}
    taken |= _reserved_tables(gpkg)
    plan = []
    for layer in layers:
        subset = layer.subsetString() or ""
        if subset and not layer.setSubsetString(""):
            continue
        try:

            source = QgsVectorLayerFeatureSource(layer)
            count = layer.featureCount()
        finally:
            if subset:
                layer.setSubsetString(subset)
        indexes = _stored_fields(layer)
        fields = QgsFields()
        for index in indexes:
            fields.append(layer.fields().at(index))
        plan.append({"id": layer.id(), "name": layer.name(), "source": source, "count": count,
                     "shown": layer.featureCount(), "subset": subset, "fields": fields, "indexes": indexes,
                     "wkb": layer.wkbType(), "crs": layer.crs(), "provider": layer.providerType(),
                     "uri": layer.source(),
                     "table": _unique(output_paths.safe_file_name(layer.name(), "layer").replace(" ", "_"), taken)})
    if not plan:
        return {}
    for item in plan:
        _WRITING[item["id"]] = (gpkg, item["table"])
    try:
        start_kept_thread(_write_run_layers, plan, gpkg, project.transformContext(), busy or _run_open, save,
                          name="ai-agent-layer-save")
    except Exception:
        _release(plan)
        raise
    return {"gpkg_path": gpkg, "layers": [item["name"] for item in plan]}


def _staged(item: dict, stage: str, context) -> str:


    from qgis.core import QgsFeature, QgsFeatureRequest

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = item["table"]
    options.fileEncoding = "UTF-8"
    if any(item["fields"].at(i).name().casefold() == "fid" for i in range(item["fields"].count())):
        options.layerOptions = ["FID=gpkg_fid"]
    writer = QgsVectorFileWriter.create(stage, item["fields"], item["wkb"], item["crs"], context, options)
    try:
        if writer.hasError():
            return writer.errorMessage() or "the staging file could not be created"
        request = QgsFeatureRequest()
        request.setSubsetOfAttributes(item["indexes"])
        for feature in item["source"].getFeatures(request):
            out = QgsFeature(item["fields"])
            out.setGeometry(feature.geometry())
            out.setAttributes([feature.attribute(index) for index in item["indexes"]])
            if not writer.addFeature(out):
                return writer.errorMessage() or "a feature could not be written"
        return writer.errorMessage() if writer.hasError() else ""
    finally:
        del writer


def _written_count(gpkg: str, table: str) -> int:

    from osgeo import ogr

    try:
        dataset = ogr.Open(gpkg, 0)
    except RuntimeError:
        return -1
    if dataset is None:
        return -1
    try:
        found = dataset.GetLayerByName(table)
        return found.GetFeatureCount() if found is not None else -1
    finally:
        dataset.Close()


def _drop_table(gpkg: str, table: str) -> None:


    from osgeo import ogr

    try:
        dataset = ogr.Open(gpkg, 1)
        if dataset is None:
            return
        try:
            for index in range(dataset.GetLayerCount()):
                if dataset.GetLayer(index).GetName().casefold() == table.casefold():
                    dataset.DeleteLayer(index)
                    break
        finally:
            dataset.Close()
    except RuntimeError as exc:
        log_warning(f"The partial table {table} stays in {gpkg}: {exc}")


def _write_one(item: dict, gpkg: str, folder: str, context) -> bool:

    from osgeo import gdal

    stage = os.path.join(folder, f"{item['table'][:40]}.gpkg")
    why = _staged(item, stage, context)
    item["source"] = None
    if why:
        log_warning(f"Layer {item['name']} not saved: {why}")
        return False
    if _written_count(gpkg, item["table"]) >= 0:

        log_warning(f"Layer {item['name']} not saved: {gpkg} already holds a table {item['table']}")
        return False
    try:
        options = gdal.VectorTranslateOptions(
            accessMode="update" if os.path.exists(gpkg) else None, layers=[item["table"]],
            options=["-gt", str(_COPY_GROUP)])
        copied = gdal.VectorTranslate(gpkg, stage, options=options)
        if copied is None:
            raise RuntimeError("GDAL wrote nothing")
        copied = None
    except RuntimeError as exc:
        log_warning(f"Layer {item['name']} not saved in {gpkg}: {exc}")
        _drop_table(gpkg, item["table"])
        return False
    if _written_count(gpkg, item["table"]) != item["count"]:
        log_warning(f"Layer {item['name']}: {gpkg} holds another count than the layer; it stays as it was")
        _drop_table(gpkg, item["table"])
        return False
    return True


def _write_run_layers(plan: list, gpkg: str, context, busy, save: bool = True) -> None:


    from osgeo import gdal

    from ..core import policy
    from ..core.background import run_on_main_thread
    from ..core.host_platform import remove_tree

    done = []
    try:
        gdal.UseExceptions()
        os.makedirs(os.path.dirname(gpkg), exist_ok=True)
        folder = policy.create_managed_temp_dir("runsave")
        try:
            done = [item for item in plan if _write_one(item, gpkg, folder, context)]
        finally:
            remove_tree(folder)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layers not written to {gpkg}: {exc}")
    finally:
        written = {item["id"] for item in done}
        for item in plan:
            item["source"] = None
        _release([item for item in plan if item["id"] not in written])
    if not done:
        return
    try:
        run_on_main_thread(_repoint_when_idle, done, gpkg, busy, 0, save, timeout=30)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layers written to {gpkg} not repointed: {exc}")
        _release(done)


def _repoint_when_idle(done: list, gpkg: str, busy, tries: int, save: bool = True) -> None:

    from qgis.PyQt.QtCore import QTimer

    from ..core.logger import log

    if busy is not None and busy():
        if tries < _IDLE_TRIES:
            QTimer.singleShot(_IDLE_WAIT_MS, lambda: _repoint_when_idle(done, gpkg, busy, tries + 1, save))
        else:
            _release(done)
            log_warning(f"Layers written to {gpkg} not repointed: a run stayed open")
        return
    project = QgsProject.instance()
    moved = []
    try:
        for item in done:
            layer = project.mapLayer(item["id"])

            if (not isinstance(layer, QgsVectorLayer) or layer.providerType() != item["provider"]
                    or layer.source() != item["uri"] or layer.isEditable()
                    or layer.featureCount() != item["shown"]):
                continue
            uri = f"{gpkg}|layername={item['table']}"
            _repoint(layer, uri, "ogr")
            if not layer.isValid():
                log_warning(f"Layer {item['name']} does not open from {uri}")
                continue
            if item["subset"] and not layer.setSubsetString(item["subset"]):
                layer.setSubsetString("")
            _save_style_inside(layer)
            moved.append(item["name"])
    finally:
        _release(done)
    if not moved:
        return
    saved = False
    if save and project.fileName():
        temp_folder_ready("agent")
        saved = project.write()
    log(f"Layers saved to {gpkg}: {', '.join(moved)}"
        + (f"; project written to {project.fileName()}" if saved else ""))


__all__ = ["register_persist_tools", "save_run_layers", "store_in_background", "writing"]
