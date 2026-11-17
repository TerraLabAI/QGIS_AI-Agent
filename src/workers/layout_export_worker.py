# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import json
import os
import sys
import time
import traceback


def _write(path: str, value: dict) -> None:
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=True, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)








_TOLERANCE_PX = 1.0

_MIN_VERTICES = 50_000


def _simplified_for_print(project, layout, dpi: int, folder: str) -> list:



    from qgis.core import (
        QgsDistanceArea,
        QgsFeature,
        QgsLayoutItemMap,
        QgsVectorFileWriter,
        QgsVectorLayer,
    )

    scales: dict = {}
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        if item.keepLayerSet():
            layers = item.layers()
        elif item.followVisibilityPreset():
            layers = project.mapThemeCollection().mapThemeVisibleLayers(item.followVisibilityPresetName())
        else:
            layers = project.layerTreeRoot().checkedLayers()
        for layer in layers:
            if layer is not None:
                scales[layer.id()] = min(scales.get(layer.id(), float("inf")), item.scale())
    done = []
    for layer_id, scale in scales.items():
        layer = project.mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid() or not scale or scale == float("inf"):
            continue
        if _type_name(layer.geometryType()) not in ("Line", "LineGeometry", "Polygon", "PolygonGeometry"):
            continue
        ground_m = scale * 0.0254 / dpi * _TOLERANCE_PX
        tolerance = ground_m
        if layer.crs().isGeographic():


            middle = layer.extent().center()
            measure = QgsDistanceArea()
            measure.setSourceCrs(layer.crs(), project.transformContext())
            measure.setEllipsoid("WGS84")
            degree = measure.measureLine(middle, middle.__class__(middle.x() + 1.0, middle.y()))
            if not degree:
                continue
            tolerance = ground_m / degree
        provider_fields = layer.dataProvider().fields()
        names = [provider_fields.at(i).name() for i in range(provider_fields.count())]
        before = after = 0
        kept = []
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            out = QgsFeature(provider_fields)
            out.setAttributes([feature[name] for name in names])
            if geometry is not None and not geometry.isEmpty():
                small = geometry.simplify(tolerance)
                if small is None or small.isEmpty():
                    small = geometry
                before += _vertices(geometry)
                after += _vertices(small)
                out.setGeometry(small)
            kept.append(out)
        if before < _MIN_VERTICES or after > before * 0.8:
            continue
        target = os.path.join(folder, f"print-{len(done)}.gpkg")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = "features"
        writer = QgsVectorFileWriter.create(target, provider_fields, layer.wkbType(), layer.crs(),
                                            project.transformContext(), options)
        if writer is None or _type_name(writer.hasError()) != "NoError":
            continue
        writer.addFeatures(kept)
        del writer
        layer.setDataSource(f"{target}|layername=features", layer.name(), "ogr")
        if not layer.isValid():
            continue
        done.append({"layer": layer.name(), "vertices_before": before, "vertices_after": after,
                     "tolerance_px": _TOLERANCE_PX})
    return done


def _type_name(value) -> str:

    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    from qgis.core import QgsVectorFileWriter, QgsWkbTypes

    for holder in (QgsWkbTypes, QgsVectorFileWriter):
        for key in ("LineGeometry", "PolygonGeometry", "NoError"):
            if getattr(holder, key, object()) == value:
                return key
    return ""


def _vertices(geometry) -> int:
    try:
        return int(geometry.constGet().nCoordinates())
    except (AttributeError, RuntimeError):
        return 0


def _layout_from_file(project, path: str):


    from qgis.core import QgsPrintLayout, QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    document = QDomDocument()
    with open(path, encoding="utf-8") as handle:
        document.setContent(handle.read())
    layout = QgsPrintLayout(project)
    if not layout.readXml(document.documentElement(), document, QgsReadWriteContext()):
        return None
    return layout


def _apply(export_settings, changed) -> None:


    for name, value in (changed or {}).items():
        if isinstance(value, dict):
            value = type(getattr(export_settings, name))(value["enum"])
        setattr(export_settings, name, value)


def _export(job: dict, held: dict) -> dict:
    started = time.monotonic()
    from qgis.core import QgsLayoutExporter, QgsProject, QgsSettings

    settings = QgsSettings()
    for key, value in {**(job.get("settings") or {}), **held}.items():
        settings.setValue(key, value)
    project = QgsProject.instance()
    if not project.read(job["project_path"]):
        return {"ok": False, "error": f"the project copy did not open: {project.error()}"}
    read = time.monotonic()
    if job.get("layout_xml"):
        layout = _layout_from_file(project, job["layout_xml"])
    else:
        layout = project.layoutManager().layoutByName(job["layout_name"])
    if layout is None:
        return {"ok": False, "error": f"layout {job['layout_name']!r} is not in the project copy"}
    fmt, path, dpi = job["format"], job["output_path"], int(job["dpi"])
    simplified = []
    if job.get("simplify_for_print", True):
        simplified = _simplified_for_print(project, layout, dpi, os.path.dirname(job["project_path"]))
    simplified_at = time.monotonic()
    exporter = QgsLayoutExporter(layout)
    if job.get("page_image"):

        from qgis.PyQt.QtCore import QSize

        index, width, height = (int(value) for value in job["page_image"])
        image = exporter.renderPageToImage(index, QSize(width, height), 0)
        saved = not image.isNull() and image.save(path, "PNG")
        return {"ok": bool(saved), "error": "" if saved else "the page did not draw", "simplified": simplified,
                "read_s": round(read - started, 2), "simplify_s": round(simplified_at - read, 2),
                "export_s": round(time.monotonic() - simplified_at, 2)}
    if fmt == "pdf":
        export_settings = QgsLayoutExporter.PdfExportSettings()
        export_settings.dpi = dpi
        for flag in ("appendGeoreference", "forceVectorOutput"):
            if job.get(flag) and hasattr(export_settings, flag):
                setattr(export_settings, flag, True)
        _apply(export_settings, job.get("export_settings"))
        result = exporter.exportToPdf(path, export_settings)
    elif fmt == "svg":
        export_settings = QgsLayoutExporter.SvgExportSettings()
        export_settings.dpi = dpi
        result = exporter.exportToSvg(path, export_settings)
    else:
        export_settings = QgsLayoutExporter.ImageExportSettings()
        export_settings.dpi = dpi
        _apply(export_settings, job.get("export_settings"))
        result = exporter.exportToImage(path, export_settings)
    message = exporter.errorMessage() if hasattr(exporter, "errorMessage") else ""
    return {"ok": True, "result": int(getattr(result, "value", result)), "error_text": str(message or ""),
            "simplified": simplified, "read_s": round(read - started, 2),
            "simplify_s": round(simplified_at - read, 2), "export_s": round(time.monotonic() - simplified_at, 2)}


def main() -> int:
    job_path, status_path = sys.argv[1], sys.argv[2]
    try:


        held = json.loads(os.environ.pop("QGIS_AI_AGENT_LAYOUT_SETTINGS", "") or "{}")
        with open(job_path, encoding="utf-8") as handle:
            job = json.load(handle)
        from qgis.core import QgsApplication

        started = time.monotonic()
        application = QgsApplication([], False)
        QgsApplication.initQgis()
        try:
            report = _export(job, held)
            report["init_s"] = round(time.monotonic() - started - report.get("read_s", 0)
                                     - report.get("simplify_s", 0) - report.get("export_s", 0), 2)
            _write(status_path, report)
        finally:
            try:
                QgsApplication.exitQgis()
            except Exception:  # nosec B110
                pass
            del application
    except Exception as exc:  # noqa: BLE001
        _write(status_path, {"ok": False, "error": str(exc), "trace": traceback.format_exc()[-1500:]})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
