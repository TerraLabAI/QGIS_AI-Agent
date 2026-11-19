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

from ..core import layer_order, output_paths, policy, security
from ..core.attribute_tables import tables_closed
from ..core.background import main_thread_invoker, run_on_main_thread, start_kept_thread
from ..core.background import still_awaited as background_still_awaited
from ..core.context import THREAD_PROPERTY
from ..core.host_platform import remove_quietly, remove_tree
from ..core.logger import log, log_warning
from ..core.qt_compat import enum_member
from ..core.snapshot_files import sqlite_read_only_uri
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._layers import _source_key, resolve_layer
from .save_guard import temp_folder_ready
from .scratch_scan import WRITING, scratch_layers


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
        background=True,
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
        path = default_gpkg_path("layers" if not project.fileName() else "")
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
    with tables_closed(layer):
        layer.setDataSource(uri, layer.name(), provider, options)
    layer.setMetadata(metadata)
    layer.triggerRepaint()


def _free_tif(folder: str, stem: str, taken: set[str]) -> str:

    while True:
        target = os.path.join(folder, f"{_unique(stem, taken)}.tif")
        if not any(os.path.exists(target + extra) for extra in ("",) + _RASTER_SIDECARS):
            return target


def _raster_target(layer, gpkg: str, taken: set[str]) -> tuple[str, str, str]:

    if layer.providerType() != "gdal":
        return "", "", f"a {layer.providerType()} raster is not a file to copy"
    source = str(layer.source() or "").split("|", 1)[0]
    if not os.path.isfile(source):
        return "", "", "its scratch file is gone"
    folder = os.path.dirname(gpkg)

    taken |= {os.path.splitext(os.path.basename(path))[0].casefold() for path, _ in list(WRITING.values())
              if path.lower().endswith(".tif")}
    stem = output_paths.safe_file_name(f"{os.path.splitext(os.path.basename(gpkg))[0]}_{layer.name()}", "raster")
    target = _free_tif(folder, stem, taken)
    if not security.fits_path(target, len(".aux.xml")):

        target = _free_tif(folder, "raster", taken)
        if not security.fits_path(target, len(".aux.xml")):
            return "", "", f"{folder} is too deep for a file name Windows opens"
    return source, target, ""


def _copy_raster(source: str, target: str) -> str:


    from osgeo import gdal

    gdal.UseExceptions()
    try:
        if os.path.splitext(source)[1].lower() in (".tif", ".tiff"):
            shutil.copyfile(source, target)
            for sidecar in _RASTER_SIDECARS:
                if os.path.isfile(source + sidecar):
                    shutil.copyfile(source + sidecar, target + sidecar)
        else:
            gdal.Translate(target, source, format="GTiff", creationOptions=_RASTER_CREATION)
        opened = gdal.Open(target)
        if opened is None or opened.RasterCount < 1:
            raise RuntimeError("the copy does not open")
        opened = None
        return ""
    except (OSError, RuntimeError) as exc:
        for extra in ("",) + _RASTER_SIDECARS:
            if os.path.exists(target + extra):
                remove_quietly(target + extra)
        return f"the copy failed: {str(exc)[:200]}"


def _make_layers_permanent(args: dict) -> dict:



    plan = run_on_main_thread(_permanent_plan, args, timeout=60)
    if "out" in plan:
        return plan["out"]
    failed: dict[str, str] = {}
    try:
        failed = _write_items(plan["items"], plan["gpkg"], plan["context"])
        try:
            return run_on_main_thread(_permanent_finish, plan, failed, args.get("save", True) is not False,
                                      timeout=120)
        except InterruptedError:

            _drop_written(plan, failed)
            raise
    finally:
        _release(plan["items"])


def _permanent_plan(args: dict) -> dict:


    from qgis.core import QgsVectorLayerFeatureSource

    project = QgsProject.instance()
    memory, temporary = scratch_layers(project)
    kinds = {layer.id(): "memory" for layer in memory}
    kinds.update({layer.id(): "scratch" for layer in temporary})
    skipped: list[dict] = []
    wanted = args.get("layers")
    if isinstance(wanted, list) and wanted:
        chosen = []
        for ref in wanted:
            layer = resolve_layer(str(ref))
            if layer is None:
                skipped.append({"name": str(ref), "skipped": "no layer by that name"})
            elif layer.id() in WRITING:
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
        return {"out": out}

    gpkg, refused = _target(args, project)
    if refused:
        return {"out": refused}
    os.makedirs(os.path.dirname(gpkg), exist_ok=True)
    taken = _gpkg_tables(gpkg)
    moved_from = ""
    if taken is None:


        fresh = _fresh_gpkg(gpkg)
        if not fresh:
            return {"out": tool_error(f"The tables of {gpkg} cannot be read, so nothing was written into it.",
                                      "INVALID_ARGS", hint="persist_gpkg_unreadable", path=gpkg)}
        moved_from, gpkg, taken = gpkg, fresh, set()
    taken |= _reserved_tables(gpkg)
    files_taken: set[str] = set()
    items: list[dict] = []
    for layer in chosen:
        name = layer.name()
        if isinstance(layer, QgsRasterLayer):
            source, target, why = _raster_target(layer, gpkg, files_taken)
            if why:
                skipped.append({"name": name, "skipped": why})
                continue
            items.append({"id": layer.id(), "name": name, "raster": True, "source": source, "target": target,
                          "provider": "gdal", "uri": layer.source(), "was": "scratch",
                          "table": os.path.splitext(os.path.basename(target))[0]})
            continue
        if not isinstance(layer, QgsVectorLayer):
            skipped.append({"name": name, "skipped": "only vector and raster layers are moved"})
            continue
        if layer.isEditable():
            skipped.append({"name": name, "skipped": "it is being edited: save or discard the edits first"})
            continue


        subset = layer.subsetString() or ""
        if subset and not layer.setSubsetString(""):
            skipped.append({"name": name, "skipped": "its filter could not be lifted to copy every feature, so "
                                                     "the layer was left as it was"})
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
        items.append({"id": layer.id(), "name": name, "source": source, "count": count,
                      "shown": layer.featureCount(), "subset": subset, "fields": fields, "indexes": indexes,
                      "wkb": layer.wkbType(), "crs": layer.crs(), "provider": layer.providerType(),
                      "uri": layer.source(), "was": kinds[layer.id()],
                      "table": _unique(output_paths.safe_file_name(name, "layer").replace(" ", "_"), taken)})
    if not items:
        return {"out": {"gpkg_path": gpkg, "made_permanent": [], "skipped": skipped}}
    if not background_still_awaited(changes_project=False):

        return {"out": {}}
    for item in items:
        WRITING[item["id"]] = (item.get("target") or gpkg, item["table"])
    return {"items": items, "gpkg": gpkg, "skipped": skipped, "moved_from": moved_from,
            "context": project.transformContext()}


def _write_items(items: list, gpkg: str, context) -> dict[str, str]:

    from osgeo import gdal

    from ..core import net

    cancelled = net.current_cancel_check()
    failed: dict[str, str] = {}
    gdal.UseExceptions()
    folder = policy.create_managed_temp_dir("runsave")
    try:
        for item in items:
            if cancelled is not None and cancelled():
                failed[item["id"]] = "the call was stopped before it was written"
                continue
            try:
                why = _write_one(item, gpkg, folder, context)
            except Exception as exc:  # noqa: BLE001
                why = f"the GeoPackage write failed: {str(exc)[:200]}"
            if why:
                failed[item["id"]] = why
    finally:
        for item in items:
            item["source"] = None
        remove_tree(folder)
    return failed


def _drop_written(plan: dict, failed: dict) -> None:

    for item in plan["items"]:
        if item["id"] in failed:
            continue
        if item.get("raster"):
            for extra in ("",) + _RASTER_SIDECARS:
                if os.path.exists(item["target"] + extra):
                    remove_quietly(item["target"] + extra)
        else:
            _drop_table(plan["gpkg"], item["table"])


def _permanent_finish(plan: dict, failed: dict, save: bool) -> dict:

    project = QgsProject.instance()
    gpkg = plan["gpkg"]
    if not background_still_awaited():

        _drop_written(plan, failed)
        return {}
    moved: list[dict] = []
    skipped = list(plan["skipped"])
    for item in plan["items"]:
        name = item["name"]
        if item["id"] in failed:
            skipped.append({"name": name, "skipped": failed[item["id"]]})
            continue
        layer = project.mapLayer(item["id"])
        try:
            entry = _repointed(layer, item, gpkg)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"make_layers_permanent: {name}: {exc}")
            entry = {"name": name, "error": str(exc)[:200]}
        if "skipped" in entry and not item.get("raster"):
            _drop_table(gpkg, item["table"])
        (moved if "was" in entry else skipped).append(entry)

    out: dict = {"gpkg_path": gpkg, "made_permanent": moved}
    if plan["moved_from"]:
        moved_from = plan["moved_from"]
        out["gpkg_note"] = (f"The tables of {moved_from} could not be read (another program may hold it), so the "
                            f"layers went to a new file and nothing in {os.path.basename(moved_from)} was touched.")
    if skipped:
        out["skipped"] = skipped
    if moved and save:
        refusal = layer_order.write_refusal(project)
        if refusal:
            out["project_note"] = f"The layers read the new files; the project is not written: {refusal}."
        elif project.fileName():
            temp_folder_ready("agent")
            from ..core.snapshot import keep_ours_across


            moved_stamp = keep_ours_across(project.fileName())
            if project.write():
                if moved_stamp is not None:
                    moved_stamp()
                out["project_saved"] = project.fileName()
            else:
                out["project_note"] = ("The layers read the new files; the project needs a write. "
                                       "save_project writes it.")
        else:
            out["project_note"] = ("The project has never been saved; the layers read the new files, and "
                                   "save_project writes the project.")
    return out


def _repointed(layer, item: dict, gpkg: str) -> dict:

    name = item["name"]
    if item.get("raster"):
        if not (isinstance(layer, QgsRasterLayer) and layer.providerType() == "gdal"
                and layer.source() == item["uri"]):
            return {"name": name, "skipped": "it changed while it was copied, so it was left as it was"}
        _repoint(layer, item["target"], "gdal")
        if not layer.isValid():
            return {"name": name, "error": f"the layer does not open from {item['target']} after the move"}
        return {"name": name, "was": "scratch", "file": item["target"]}

    if (not isinstance(layer, QgsVectorLayer) or layer.providerType() != item["provider"]
            or layer.source() != item["uri"] or layer.isEditable() or layer.featureCount() != item["shown"]):
        return {"name": name, "skipped": "it changed while it was written, so it was left as it was"}
    uri = f"{gpkg}|layername={item['table']}"
    _repoint(layer, uri, "ogr")
    if not layer.isValid():
        return {"name": name, "error": f"the layer does not open from {uri} after the move"}
    entry = {"name": name, "was": item["was"], "table": item["table"], "features": item["count"]}
    subset = item["subset"]
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



















_COPY_GROUP = 5000

_IDLE_WAIT_MS = 2000
_IDLE_TRIES = 900
def _reserved_tables(gpkg: str) -> set[str]:
    key = os.path.normcase(os.path.abspath(gpkg))
    return {table.casefold() for path, table in list(WRITING.values())
            if os.path.normcase(os.path.abspath(path)) == key}


def _release(items) -> None:
    for item in items:
        WRITING.pop(item["id"], None)


def save_run_layers(layer_ids, busy=None, then=None, before_save=None) -> int:



    project = QgsProject.instance()
    layers = [project.mapLayer(str(layer_id)) for layer_id in dict.fromkeys(layer_ids or ())]
    layers = [layer for layer in layers if isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory"]
    return len(store_in_background(layers, busy, then=then, before_save=before_save).get("layers") or ())


def _run_open() -> bool:

    return layer_order.current_run() is not None


def store_in_background(layers, busy=None, save: bool = True, then=None, before_save=None) -> dict:








    from qgis.core import QgsVectorLayerFeatureSource

    project = QgsProject.instance()
    layers = [layer for layer in layers if isinstance(layer, (QgsVectorLayer, QgsRasterLayer)) and layer.isValid()
              and not (isinstance(layer, QgsVectorLayer) and layer.isEditable()) and layer.id() not in WRITING]
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
    files_taken: set[str] = set()
    for layer in layers:
        if isinstance(layer, QgsRasterLayer):


            source, target, why = _raster_target(layer, gpkg, files_taken)
            if why:
                log_warning(f"Raster {layer.name()} not saved: {why}")
                continue
            plan.append({"id": layer.id(), "name": layer.name(), "raster": True, "source": source,
                         "target": target, "provider": "gdal", "uri": layer.source(),
                         "table": os.path.splitext(os.path.basename(target))[0]})
            continue
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
        WRITING[item["id"]] = (item.get("target") or gpkg, item["table"])
    try:
        start_kept_thread(_write_run_layers, plan, gpkg, project.transformContext(), busy or _run_open, save,
                          then, before_save, name="ai-agent-layer-save")
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


def _write_one(item: dict, gpkg: str, folder: str, context) -> str:


    from osgeo import gdal

    if item.get("raster"):
        why = _copy_raster(item["source"], item["target"])
        if why:
            log_warning(f"Raster {item['name']} not saved: {why}")
        return why

    stage = os.path.join(folder, f"{item['table'][:40]}.gpkg")
    why = _staged(item, stage, context)
    item["source"] = None
    if why:
        log_warning(f"Layer {item['name']} not saved: {why}")
        return f"the GeoPackage write failed: {why[:200]}"
    if _written_count(gpkg, item["table"]) >= 0:

        log_warning(f"Layer {item['name']} not saved: {gpkg} already holds a table {item['table']}")
        return f"{os.path.basename(gpkg)} already holds a table {item['table']}"
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
        return f"the GeoPackage write failed: {str(exc)[:200]}"
    written = _written_count(gpkg, item["table"])
    if written != item["count"]:

        log_warning(f"Layer {item['name']}: {gpkg} holds another count than the layer; it stays as it was")
        _drop_table(gpkg, item["table"])
        return f"the copy holds {max(written, 0)} of {item['count']} features, so the layer was left as it was"
    return ""


def _write_run_layers(plan: list, gpkg: str, context, busy, save: bool = True, then=None,
                      before_save=None) -> None:


    from osgeo import gdal

    done = []
    try:
        gdal.UseExceptions()
        os.makedirs(os.path.dirname(gpkg), exist_ok=True)
        folder = policy.create_managed_temp_dir("runsave")
        try:
            done = [item for item in plan if not _write_one(item, gpkg, folder, context)]
        finally:
            remove_tree(folder)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layers not written to {gpkg}: {exc}")
    finally:
        written = {item["id"] for item in done}
        for item in plan:
            item["source"] = None
        _release([item for item in plan if item["id"] not in written])
    try:
        if not done:
            if then is not None:
                run_on_main_thread(then, timeout=30)
            return
        run_on_main_thread(_repoint_when_idle, done, gpkg, busy, 0, save, then, before_save, timeout=30)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layers written to {gpkg} not repointed: {exc}")
        _release(done)
        if then is not None:


            main_thread_invoker().invoke(then)


def _repoint_when_idle(done: list, gpkg: str, busy, tries: int, save: bool = True, then=None,
                       before_save=None) -> None:

    from qgis.PyQt.QtCore import QTimer

    if busy is not None and busy() and tries < _IDLE_TRIES:
        QTimer.singleShot(_IDLE_WAIT_MS, lambda: _repoint_when_idle(done, gpkg, busy, tries + 1, save, then,
                                                                    before_save))
        return
    try:
        _repoint_now(done, gpkg, busy, save, before_save)
    finally:
        if then is not None:
            then()


def _repoint_now(done: list, gpkg: str, busy, save: bool, before_save=None) -> None:
    if busy is not None and busy():
        _release(done)
        log_warning(f"Layers written to {gpkg} not repointed: a run stayed open")
        return
    project = QgsProject.instance()
    moved = []
    try:
        for item in done:
            layer = project.mapLayer(item["id"])
            if item.get("raster"):
                if (isinstance(layer, QgsRasterLayer) and layer.providerType() == "gdal"
                        and layer.source() == item["uri"]):
                    _repoint(layer, item["target"], "gdal")
                    if layer.isValid():
                        moved.append(item["name"])
                    else:
                        log_warning(f"Raster {item['name']} does not open from {item['target']}")
                continue

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
    refusal = layer_order.write_refusal(project) if save and project.fileName() else ""
    if refusal:
        log_warning(f"Project not written after its layers' save: {refusal}.")
    elif save and project.fileName():
        temp_folder_ready("agent")

        written = None
        try:
            if before_save is not None:
                written = before_save(project.fileName())
            else:
                from ..core.snapshot import keep_ours_across

                written = keep_ours_across(project.fileName())
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Project file not copied before its save: {exc}")
        saved = project.write()
        if saved and written is not None:
            try:
                written()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Project file copy not recorded after its save: {exc}")
    log(f"Layers saved to {gpkg}: {', '.join(moved)}"
        + (f"; project written to {project.fileName()}" if saved else ""))


__all__ = ["register_persist_tools", "save_run_layers", "store_in_background"]


def default_gpkg_path(layer_name: str = "") -> str | None:







    fname = QgsProject.instance().fileName()
    if not fname:
        folder = output_paths.exports_folder()
        stem = output_paths.safe_file_name(layer_name, "layers")
        path, number = os.path.join(folder, f"{stem}.gpkg"), 2
        while os.path.exists(path):
            path, number = os.path.join(folder, f"{stem}_{number}.gpkg"), number + 1
        os.makedirs(folder, exist_ok=True)
        return path
    directory = os.path.dirname(fname)
    stem = os.path.splitext(os.path.basename(fname))[0]
    beside = os.path.join(directory, f"{stem}_data.gpkg")
    room = len("-journal")
    if security.fits_path(beside, room):
        return beside




    short = os.path.join(directory, "_data.gpkg")
    if security.fits_path(short, room):
        return short
    return os.path.join(output_paths.exports_folder(), f"{output_paths.safe_file_name(stem, 'layers')}_data.gpkg")


def scratch_layers_summary(project: QgsProject) -> tuple[list[dict], list[dict]]:










    def entry(layer) -> dict:
        if not isinstance(layer, QgsVectorLayer):
            if isinstance(layer, QgsRasterLayer):
                return {"name": layer.name(), "layer_type": "raster"}
            return {"name": layer.name()}
        try:
            count = layer.featureCount()
        except Exception:  # noqa: BLE001
            count = None
        return {"name": layer.name(), "feature_count": count}

    memory_layers, temporary_layers = scratch_layers(project)
    return [entry(layer) for layer in memory_layers], [entry(layer) for layer in temporary_layers]


def agent_layers_made_durable(project, save: bool = True, busy=None) -> dict:










    if not project.fileName():
        return {}
    memory, temporary = scratch_layers(project)
    ours = [layer for layer in memory + temporary if layer.customProperty(THREAD_PROPERTY, "")]
    if not ours:
        return {}
    try:
        sent = store_in_background(ours, busy, save)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Agent layers not stored beside the project: {exc}")
        return {}
    if not sent:
        return {}
    return {"gpkg_path": sent["gpkg_path"], "layers": sent["layers"], "writing": sent["layers"]}
