# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import os
import shutil

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
    dataset = None
    try:
        from osgeo import gdal

        dataset = gdal.OpenEx(path, gdal.OF_VECTOR | gdal.OF_RASTER | gdal.OF_READONLY)
        if dataset is None:
            return None
        names = {dataset.GetLayerByIndex(i).GetName().casefold() for i in range(dataset.GetLayerCount())}
        for entry in (dataset.GetMetadata("SUBDATASETS") or {}).values():
            if entry.startswith("GPKG:"):
                names.add(entry.rsplit(":", 1)[-1].casefold())
        return names
    except Exception as exc:  # noqa: BLE001
        log_warning(f"make_layers_permanent: the tables of {path} cannot be listed: {exc}")
        return None
    finally:
        dataset = None


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
                                  "End gpkg_path in .gpkg, or leave it out for <project>_data.gpkg beside the project.")
    else:
        path = _default_gpkg_path("layers" if not project.fileName() else "")
    if _source_key(path).startswith(_source_key(policy.AGENT_TMP_DIR).rstrip("/") + "/"):
        return "", tool_error("gpkg_path is inside the agent's scratch folder, which is pruned.", "INVALID_ARGS",
                              "Leave gpkg_path out, or name a file beside the project or in the user's folders.")
    error = security.validate_path(path, write=True)
    if error:
        return "", tool_error(error, "PERMISSION_DENIED",
                              "Pick a GeoPackage under the project folder or the user's home folder.")
    if not security.fits_path(path, len("-journal")):
        return "", tool_error(f"{path} is longer than Windows opens.", "INVALID_ARGS",
                              "Pass a shorter gpkg_path.")
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
    layer.setDataSource(uri, layer.name(), provider, options)
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
            entry["note"] = ("Every feature was kept, but the filter does not read on the GeoPackage, so the layer "
                             "now shows all of them: set it again with set_layer_filter in SQL.")
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
                              "INVALID_ARGS", "Close the program holding it, or pass gpkg_path with a new file name.")
        moved_from, gpkg, taken = gpkg, fresh, set()
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
            if project.write():
                out["project_saved"] = project.fileName()
            else:
                out["project_note"] = ("The layers read the new files, but the project could not be written: "
                                       "call save_project.")
        else:
            out["project_note"] = ("The project has never been saved; the layers read the new files, and "
                                   "save_project writes the project.")
    return out


__all__ = ["register_persist_tools"]
