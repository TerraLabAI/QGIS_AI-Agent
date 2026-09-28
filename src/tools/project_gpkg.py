# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import os
import re
import uuid
from urllib.parse import quote

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsPrintLayout,
    QgsProject,
    QgsRasterLayer,
    QgsReadWriteContext,
    QgsRelation,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)
from qgis.PyQt.QtXml import QDomDocument

from ..core import tuning
from ..core.background import heartbeat
from ..core.host_platform import release_pooled_handles, remove_quietly, retry_file_op
from ..core.logger import log_warning
from ..core.output_paths import safe_file_name
from ..core.security import fits_path, validate_path
from ..core.tool_registry import tool_error




GPKG_RASTER_MAX_CELLS = 100_000_000

_WRITTEN_PROVIDERS = frozenset({"ogr", "delimitedtext", "spatialite", "gpx", "memory", "virtual"})
_STOP_EVERY = 500
_SIDECARS = (".aux.xml", ".ovr", ".tfw", ".tifw", ".wld", ".prj", ".jgw", ".pgw", ".hdr", ".rrd", ".aux")


def _is_local_file(path: str) -> bool:
    lowered = path.lower()
    return bool(path) and not lowered.startswith(("/vsi", "http:", "https:", "ftp:")) and os.path.isfile(path)


def _source_path(layer) -> str:
    from .gis_case_tools import _source_path as decoded

    return decoded(layer)


def _table_name(name: str, taken: set) -> str:

    base = re.sub(r"\W+", "_", name or "", flags=re.UNICODE).strip("_")[:60] or "layer"
    if base[0].isdigit() or base.lower().startswith(("gpkg_", "rtree_", "sqlite_")):
        base = f"t_{base}"
    candidate, number = base, 2
    while candidate.lower() in taken:
        candidate = f"{base}_{number}"
        number += 1
    taken.add(candidate.lower())
    return candidate


def _raster_fits(layer) -> str:

    provider = layer.dataProvider()
    bands = provider.bandCount()
    cells = bands * layer.width() * layer.height()
    most = tuning.ceiling("gpkg_raster_max_cells", GPKG_RASTER_MAX_CELLS, 10_000_000)
    if cells > most:
        return f"{cells:,} cells, over the {most:,} a GeoPackage raster table takes here"
    kind = provider.dataType(1)
    if kind == Qgis.DataType.Byte and 1 <= bands <= 4:
        return ""
    if kind in (Qgis.DataType.Int16, Qgis.DataType.UInt16, Qgis.DataType.Float32) and bands == 1:
        return ""
    return "a GeoPackage raster holds 1 to 4 byte bands or one 16-bit or float band"


def snapshot(args: dict) -> dict:

    project = QgsProject.instance()
    project_path = project.fileName() or ""
    stem = os.path.splitext(os.path.basename(project_path))[0] if project_path else "project"
    output = os.path.abspath(os.path.expanduser(args["output_path"]))
    extension = os.path.splitext(output)[1].lower()
    if os.path.isdir(output) or not extension:
        output = os.path.join(output, f"{safe_file_name(stem, 'project')}.gpkg")
    elif extension != ".gpkg":
        output = os.path.splitext(output)[0] + ".gpkg"
    error = validate_path(output, write=True)
    if error:
        return {"_error": error}
    folder = os.path.dirname(output)
    name = os.path.splitext(os.path.basename(output))[0]
    stage = os.path.join(folder, f".{name}.{uuid.uuid4().hex[:8]}.part.gpkg")
    beside = os.path.join(folder, f"{safe_file_name(name, 'project')}_rasters")
    if not fits_path(stage) or not fits_path(os.path.join(beside, "x" * 40)):
        return tool_error("The path is too long for QGIS on Windows (260 characters).", "INVALID_ARGS",
                          "A shorter folder or file name fits.")
    wanted = os.path.normcase(os.path.normpath(output))
    ordered = [node.layer() for node in project.layerTreeRoot().findLayers() if node.layer() is not None]
    ordered += [layer for layer in project.mapLayers().values() if layer not in ordered]
    taken: set = set()
    plan = []
    for layer in ordered:
        path = _source_path(layer)
        if path and os.path.normcase(os.path.normpath(path)) == wanted:
            return tool_error(f"{layer.name()!r} reads {output}; the package cannot replace a file the project uses.",
                              "INVALID_ARGS", "Another file name avoids it.")
        entry = {"id": layer.id(), "name": layer.name()}
        if isinstance(layer, QgsVectorLayer) and layer.providerType() in _WRITTEN_PROVIDERS and (
                layer.providerType() in ("memory", "virtual") or _is_local_file(path)):
            entry.update(kind="table", table=_table_name(layer.name(), taken),
                         source=QgsVectorLayerFeatureSource(layer), fields=layer.fields(),
                         wkb=layer.wkbType(), crs=QgsCoordinateReferenceSystem(layer.crs()),
                         keep_fid=layer.providerType() == "ogr" and path.lower().endswith(".gpkg"))
        elif isinstance(layer, QgsRasterLayer) and layer.providerType() == "gdal" and _is_local_file(path):
            why = _raster_fits(layer)
            entry.update(kind="raster_table" if not why else "raster_beside", path=path, why=why,
                         table=_table_name(layer.name(), taken),
                         byte=layer.dataProvider().dataType(1) == Qgis.DataType.Byte)
        else:
            entry.update(kind="kept", why="a web or database source stays an address" if not _is_local_file(path)
                         else "this layer type is not written into a GeoPackage; it keeps its file")
        plan.append(entry)
    return {"output": output, "stage": stage, "beside": beside, "name": name, "plan": plan,
            "transform": project.transformContext()}


def _copy(source: str, target: str, cancelled, made: list | None = None) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if made is not None:
        made.append(target)
    with open(source, "rb") as reader, open(target, "wb") as writer:
        while True:
            if cancelled is not None and cancelled():
                raise InterruptedError("Project packaging was cancelled")
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            writer.write(chunk)
            heartbeat()


def _write_table(stage: str, entry: dict, transform, first: bool, cancelled) -> str:

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = entry["table"]
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = (QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile if first
                                    else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer)
    has_fid = any(field.name().lower() == "fid" for field in entry["fields"])
    if has_fid and not entry["keep_fid"]:

        options.layerOptions = ["FID=pkg_fid"]
    writer = QgsVectorFileWriter.create(stage, entry["fields"], entry["wkb"], entry["crs"], transform, options)
    try:
        if writer.hasError() != QgsVectorFileWriter.WriterError.NoError:
            return writer.errorMessage() or "the table could not be created"
        for count, feature in enumerate(entry["source"].getFeatures()):
            if count % _STOP_EVERY == 0:
                heartbeat()
                if cancelled is not None and cancelled():
                    raise InterruptedError("Project packaging was cancelled")
            if not writer.addFeature(feature):
                return writer.errorMessage() or f"feature {feature.id()} could not be written"
        return ""
    finally:
        del writer


def _write_raster(stage: str, entry: dict, cancelled) -> str:
    from osgeo import gdal

    def progress(_fraction, _message, _data):
        heartbeat()
        return 0 if cancelled is not None and cancelled() else 1

    options = [f"RASTER_TABLE={entry['table']}", "TILE_FORMAT=" + ("PNG" if entry["byte"] else "TIFF")]
    if os.path.exists(stage):
        options.append("APPEND_SUBDATASET=YES")
    try:
        result = gdal.Translate(stage, entry["path"], format="GPKG", creationOptions=options, callback=progress)
    except RuntimeError as exc:
        return str(exc)
    if result is None:
        if cancelled is not None and cancelled():
            raise InterruptedError("Project packaging was cancelled")
        return gdal.GetLastErrorMsg() or "GDAL could not write the raster table"
    result = None
    return ""


def _copy_beside(entry: dict, beside: str, cancelled, made: list) -> str:

    source = entry["path"]
    target = os.path.join(beside, os.path.basename(source))
    _copy(source, target, cancelled, made)
    folder, base = os.path.split(source)
    stem = os.path.splitext(base)[0]
    for suffix in _SIDECARS:
        for sidecar in (source + suffix, os.path.join(folder, stem + suffix)):
            if os.path.isfile(sidecar) and not os.path.exists(os.path.join(beside, os.path.basename(sidecar))):
                _copy(sidecar, os.path.join(beside, os.path.basename(sidecar)), cancelled, made)
    return target


def write(snap: dict, cancelled) -> dict:

    stage, written, first, made = snap["stage"], {}, True, []
    try:
        for entry in snap["plan"]:
            if entry["kind"] == "table":
                error = _write_table(stage, entry, snap["transform"], first, cancelled)
                if error:
                    entry.update(kind="kept", why=f"could not be written: {error[:200]}")
                    continue
                first = False
                written[entry["id"]] = "table"
        for entry in snap["plan"]:
            if entry["kind"] == "raster_table":
                error = _write_raster(stage, entry, cancelled)
                if error:
                    entry.update(kind="raster_beside", why=f"GDAL could not write it as a table: {error[:200]}")
                else:
                    written[entry["id"]] = "raster_table"
            if entry["kind"] == "raster_beside":
                entry["new_path"] = _copy_beside(entry, snap["beside"], cancelled, made)
                written[entry["id"]] = "raster_beside"
        if not os.path.exists(stage):

            from osgeo import ogr

            created = ogr.GetDriverByName("GPKG").CreateDataSource(stage)
            created = None  # noqa: F841
        retry_file_op(os.replace, stage, snap["output"])
    except BaseException:

        for path in [stage, *made]:
            remove_quietly(path)
        raise
    return written


def _layer_element(layer, entry: dict, output: str):

    doc = QDomDocument("qgis")
    element = doc.createElement("maplayer")
    doc.appendChild(element)
    context = QgsReadWriteContext()
    layer.writeLayerXml(element, doc, context)
    kind = entry["kind"]
    if kind == "kept":
        return doc, element
    if kind == "table":
        source, provider = f"{output}|layername={entry['table']}", "ogr"
    elif kind == "raster_table":
        source, provider = f"GPKG:{output}:{entry['table']}", "gdal"
    else:
        source, provider = layer.source().replace(entry["path"], entry["new_path"]), "gdal"
    for tag, text in (("datasource", source), ("provider", provider)):
        node = element.firstChildElement(tag)
        if node.isNull():
            continue
        while node.hasChildNodes():
            node.removeChild(node.firstChild())
        node.appendChild(doc.createTextNode(text))
    return doc, element


def _copy_project_settings(live, package) -> None:
    package.setCrs(live.crs())
    package.setEllipsoid(live.ellipsoid())
    package.setTitle(live.title())
    package.setTransformContext(live.transformContext())
    package.setBackgroundColor(live.backgroundColor())
    package.setSelectionColor(live.selectionColor())
    package.setCustomVariables(live.customVariables())
    for setter, getter in (("setDistanceUnits", "distanceUnits"), ("setAreaUnits", "areaUnits")):
        try:
            getattr(package, setter)(getattr(live, getter)())
        except Exception as exc:  # noqa: BLE001
            log_warning(f"package_project: {getter} not copied: {exc}")
    try:
        from qgis.utils import iface

        canvas = iface.mapCanvas() if iface is not None else None
        if canvas is not None:
            from qgis.core import QgsReferencedRectangle

            package.viewSettings().setDefaultViewExtent(
                QgsReferencedRectangle(canvas.extent(), canvas.mapSettings().destinationCrs()))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"package_project: view extent not copied: {exc}")


def _copy_tree(live, package) -> None:
    root = package.layerTreeRoot()
    root.removeAllChildren()
    for child in live.layerTreeRoot().children():
        root.addChildNode(child.clone())
    root.resolveReferences(package)

    for node in list(root.findLayers()):
        if node.layer() is None:
            node.parent().removeChildNode(node)
    live_root = live.layerTreeRoot()
    if live_root.hasCustomLayerOrder():
        order = [package.mapLayer(layer.id()) for layer in live_root.customLayerOrder()]
        root.setCustomLayerOrder([layer for layer in order if layer is not None])
        root.setHasCustomLayerOrder(True)


def _copy_extras(live, package) -> list:

    missed = []
    try:
        doc = QDomDocument("qgis")
        doc.appendChild(doc.createElement("qgis"))
        live.mapThemeCollection().writeXml(doc)
        package.mapThemeCollection().readXml(doc)
    except Exception as exc:  # noqa: BLE001
        missed.append(f"map themes ({exc})")
    for relation in live.relationManager().relations().values():
        try:
            doc = QDomDocument("qgis")
            holder = doc.createElement("relations")
            doc.appendChild(holder)
            relation.writeXml(holder, doc)
            copied = QgsRelation.createFromXml(holder.firstChild(), QgsReadWriteContext(), package.relationManager())
            package.relationManager().addRelation(copied)
        except Exception as exc:  # noqa: BLE001
            missed.append(f"relation {relation.name()} ({exc})")
    for layout in live.layoutManager().printLayouts():
        try:
            doc = QDomDocument("qgis")
            context = QgsReadWriteContext()
            element = layout.writeXml(doc, context)
            doc.appendChild(element)
            copied = QgsPrintLayout(package)
            copied.readXml(element, doc, context)
            copied.setName(layout.name())
            package.layoutManager().addLayout(copied)
        except Exception as exc:  # noqa: BLE001
            missed.append(f"layout {layout.name()} ({exc})")
    return missed


def finish(snap: dict, written: dict) -> dict:

    live = QgsProject.instance()
    output = snap["output"]
    package = QgsProject()
    added = []
    try:
        _copy_project_settings(live, package)
        styles = 0
        for entry in snap["plan"]:
            layer = live.mapLayer(entry["id"])
            if layer is None:
                continue
            if entry["kind"] != "kept" and entry["id"] not in written:
                entry.update(kind="kept", why="not written")
            _doc, element = _layer_element(layer, entry, output)
            if not package.readLayer(element):
                log_warning(f"package_project: {layer.name()} could not be read back into the package")
                continue
            copied = package.mapLayer(entry["id"])
            if copied is None:
                continue
            added.append(copied)
            if entry["kind"] == "table" and isinstance(copied, QgsVectorLayer) and copied.isValid():
                saver = getattr(copied, "saveStyleToDatabaseV2", None) or copied.saveStyleToDatabase
                try:
                    saver(entry["table"], "", True, "")
                    styles += 1
                except Exception as exc:  # noqa: BLE001
                    log_warning(f"package_project: style of {layer.name()} not stored: {exc}")
        for copied in added:
            copied.resolveReferences(package)
        _copy_tree(live, package)
        missed = _copy_extras(live, package)
        package.setPresetHomePath("")
        uri = f"geopackage:{output}?projectName={quote(snap['name'])}"
        if not package.write(uri):
            return {"_error": f"QGIS could not store the project in {output}: {package.error()}"}
    finally:
        tables = [copied.source() for copied in added if copied.providerType() == "ogr"
                  and copied.source().startswith(output)]
        added.clear()
        package.clear()
        del package
        if tables:

            release_pooled_handles(QgsVectorLayer(tables[0], "release", "ogr"))
    tables = [e for e in snap["plan"] if e["kind"] == "table"]
    rasters_in = [e for e in snap["plan"] if e["kind"] == "raster_table"]
    beside = [e for e in snap["plan"] if e["kind"] == "raster_beside"]
    kept = [e for e in snap["plan"] if e["kind"] == "kept"]
    out = {
        "package_path": output,
        "format": "gpkg",
        "project_name": snap["name"],
        "tables": [{"layer": e["name"], "table": e["table"]} for e in tables],
        "rasters_in_gpkg": [{"layer": e["name"], "table": e["table"]} for e in rasters_in],
        "styles_in_gpkg": styles,
        "file_size": os.path.getsize(output),
        "size_units": "bytes",
        "open_with": ("In QGIS: Browser panel, GeoPackage, this file, then the project inside it; or "
                      "Project > Open From > GeoPackage."),
    }
    if beside:
        out["rasters_beside"] = {"folder": snap["beside"],
                                 "layers": [{"layer": e["name"], "why": e["why"]} for e in beside],
                                 "note": "keep this folder next to the GeoPackage: the project reads them there"}
    if kept:
        out["kept_sources"] = [{"layer": e["name"], "why": e["why"]} for e in kept]
    if missed:
        out["not_copied"] = missed
    return out


def package(args: dict) -> dict:

    from ..core import net
    from ..core.background import run_on_main_thread

    cancelled_answer = tool_error("Project packaging was cancelled.", "CANCELLED",
                                  "package_project can be called again for the GeoPackage.")
    try:
        snap = run_on_main_thread(snapshot, args, timeout=20)
        if "_error" in snap:
            return snap
        written = write(snap, net.current_cancel_check())
        for entry in snap["plan"]:
            entry.pop("source", None)
        return run_on_main_thread(finish, snap, written, timeout=120)
    except InterruptedError:
        return cancelled_answer
