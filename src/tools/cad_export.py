# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















from __future__ import annotations

import codecs
import os
import re

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsDxfExport,
    QgsFeatureRequest,
    QgsFillSymbol,
    QgsMapSettings,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QBuffer, QIODevice

from ..core import limits
from ..core.qt_compat import enum_member
from ..core.tool_registry import tool_error



CODE_PAGES = {
    "cp1252": "ANSI_1252", "cp1250": "ANSI_1250", "cp1251": "ANSI_1251", "cp1253": "ANSI_1253",
    "cp1254": "ANSI_1254", "cp1255": "ANSI_1255", "cp1256": "ANSI_1256", "cp1257": "ANSI_1257",
    "cp1258": "ANSI_1258", "cp874": "ANSI_874", "cp932": "ANSI_932", "gbk": "ANSI_936",
    "cp949": "ANSI_949", "cp950": "ANSI_950",
}


_SUPERSETS = {"euc_kr": "cp949", "shift_jis": "cp932", "gb2312": "gbk", "big5": "cp950",
              "iso8859-1": "cp1252", "tis-620": "cp874"}


AUTO_ORDER = ("cp1252", "cp1250", "cp1251", "cp1253", "cp1254", "cp1255", "cp1256", "cp1257",
              "cp1258", "cp874", "cp932", "cp949", "gbk", "cp950")
_CODEPAGE_LINE = re.compile(rb"(\$DWGCODEPAGE\r?\n[ \t]*3\r?\n)[^\r\n]*")
_DEFAULT_SCALE = 1000.0


def _escape_unicode(error):

    return "".join(f"\\U+{ord(char):04X}" for char in error.object[error.start:error.end]), error.end


codecs.register_error("ai_agent_dxf_unicode", _escape_unicode)


def code_page(name: str) -> str | None:

    text = str(name or "").strip().lower()
    match = re.fullmatch(r"ansi[_-]?(\d+)", text)
    if match:
        text = "cp" + match.group(1)
    try:
        codec = codecs.lookup(text).name
    except LookupError:
        return None
    codec = _SUPERSETS.get(codec, codec)
    return codec if codec in CODE_PAGES else None


def _symbology_scale() -> float:

    try:
        from qgis.utils import iface

        scale = float(iface.mapCanvas().scale()) if iface is not None else 0.0
    except Exception:  # noqa: BLE001
        scale = 0.0
    return scale if scale > 0 else _DEFAULT_SCALE


def _outline_renderer(layer):

    color, width = "0,0,0,255", "0.26"
    try:
        from qgis.core import QgsRenderContext

        symbols = layer.renderer().symbols(QgsRenderContext()) if layer.renderer() else []
        first = symbols[0].symbolLayer(0) if symbols and symbols[0].symbolLayerCount() else None
        if first is not None and hasattr(first, "strokeColor"):
            stroke = first.strokeColor()
            color = f"{stroke.red()},{stroke.green()},{stroke.blue()},255"
            width = str(first.strokeWidth() or 0.26)
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    return QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"style": "no", "outline_color": color, "outline_width": width, "outline_style": "solid"}))


def _export_source(layer, selected_only: bool, polygons_as_lines: bool):

    is_polygon = QgsWkbTypes.geometryType(layer.wkbType()) == enum_member(Qgis, "GeometryType", "Polygon")
    lines = polygons_as_lines and is_polygon
    if not selected_only and not lines:
        return layer, None
    request = QgsFeatureRequest()
    if selected_only:
        request.setFilterFids(layer.selectedFeatureIds())
    copy = layer.materialize(request)
    copy.setName(layer.name())
    if layer.labeling() is not None:
        copy.setLabeling(layer.labeling().clone())
    copy.setLabelsEnabled(layer.labelsEnabled())
    if lines:
        copy.setRenderer(_outline_renderer(layer))
    elif layer.renderer() is not None:
        copy.setRenderer(layer.renderer().clone())
    return copy, copy


def _readback(path: str) -> dict:




    try:
        from osgeo import ogr

        dataset = ogr.Open(path, 0)
    except Exception:  # noqa: BLE001
        return {}
    if dataset is None:
        return {}
    entities = texts = hatches = 0
    cad_layers: set[str] = set()
    samples: list[str] = []
    try:
        table = dataset.GetLayer(0)
        definition = table.GetLayerDefn()
        fields = {definition.GetFieldDefn(i).GetName() for i in range(definition.GetFieldCount())}
        table.SetIgnoredFields(["OGR_GEOMETRY"])
        for feature in table:
            entities += 1
            sub = str(feature.GetField("SubClasses") or "") if "SubClasses" in fields else ""
            text = feature.GetField("Text") if "Text" in fields else None
            if "Hatch" in sub:
                hatches += 1
            elif "Text" in sub and text:
                texts += 1
                if len(samples) < 3:
                    samples.append(str(text)[:60])
            if "Layer" in fields and feature.GetField("Layer"):
                cad_layers.add(str(feature.GetField("Layer")))
    finally:
        dataset = None  # noqa: F841
    return {"entities": entities, "labels_in_file": texts, "label_samples": samples, "hatches": hatches,
            "cad_layers": sorted(cad_layers)[:20], "cad_layer_count": len(cad_layers)}


def export_dxf(layer, path: str, args: dict) -> dict:

    from .layer_io_tools import (
        _discard_staged_write,
        _publish_staged_write,
        _release_layers_at_path,
        _staging_path,
    )

    asked = str(args.get("encoding") or "").strip()
    codec = code_page(asked) if asked else None
    if asked and codec is None:
        return tool_error(
            f"'{asked}' is not a DXF code page.", "INVALID_ARGS",
            "Pass one of cp1252 (Western), cp1250, cp1251 (Cyrillic), cp1253, cp1254, cp1255, cp1256, cp1257, "
            "cp1258, cp874 (Thai), cp932 (Japanese), gbk (Simplified Chinese), cp949 (Korean), cp950 "
            "(Traditional Chinese), or leave encoding out to pick the first page that holds every label.")
    selected_only = bool(args.get("selected_only", False))
    count = layer.selectedFeatureCount() if selected_only else layer.featureCount()
    if selected_only and count == 0:
        return tool_error(f"Nothing is selected in '{layer.name()}'.", "INVALID_ARGS",
                          "Select the features to export first, or drop selected_only.")
    ceiling = int(limits.current("SYNC_FEATURE_LOOP_MAX"))
    if count > ceiling:
        return tool_error(
            f"'{layer.name()}' has {count} features to draw; a DXF export runs on QGIS's main thread and is "
            f"capped at {ceiling} here.", "INVALID_ARGS",
            "Subset to the area CAD needs first (select the features and pass selected_only true, or clip), "
            "which is what a CAD drawing wants anyway. For the whole layer, run_processing native:dxfexport runs "
            "in the background, but on QGIS 4 it writes non-Latin text as UTF-8 under an ANSI header.")
    field = str(args.get("cad_layer_field") or "").strip()
    field_index = -1
    if field:
        field_index = layer.fields().lookupField(field)
        if field_index < 0:
            return tool_error(f"'{layer.name()}' has no field '{field}'.", "INVALID_ARGS",
                              f"Pick one of: {', '.join(layer.fields().names()[:30])}.")

    crs = QgsCoordinateReferenceSystem(args["crs"]) if args.get("crs") else layer.crs()
    if not crs.isValid():
        return tool_error(f"Invalid CRS: {args.get('crs') or layer.crs().authid()}", "INVALID_ARGS",
                          "Pass the drawing's projected CRS, for example EPSG:5186 or EPSG:2154.")
    polygons_as_lines = bool(args.get("polygons_as_lines", False))
    source, copy = _export_source(layer, selected_only, polygons_as_lines)

    dxf = QgsDxfExport()
    settings = QgsMapSettings()
    settings.setTransformContext(QgsProject.instance().transformContext())
    dxf.setMapSettings(settings)
    dxf.addLayers([QgsDxfExport.DxfLayer(source, field_index)])
    scale = _symbology_scale()
    dxf.setSymbologyScale(scale)
    dxf.setSymbologyExport(enum_member(Qgis, "FeatureSymbologyExport", "PerFeature"))
    dxf.setDestinationCrs(crs)
    dxf.setForce2d(False)
    buffer = QBuffer()
    buffer.open(enum_member(QIODevice, "OpenModeFlag", "WriteOnly"))
    result = dxf.writeToFile(buffer, "UTF-8")
    buffer.close()
    del copy
    if result != enum_member(QgsDxfExport, "ExportResult", "Success"):
        return tool_error(f"QGIS's DXF writer refused the export ({result}).", "EXECUTION_FAILED",
                          "Check the layer has features in its extent and a valid CRS.")
    try:
        text = bytes(buffer.data()).decode("utf-8")
    except UnicodeDecodeError as exc:
        return tool_error(f"QGIS wrote a DXF that is not UTF-8 ({exc}).", "EXECUTION_FAILED",
                          "Run run_processing native:dxfexport with an explicit ENCODING instead.")


    letters = "".join(sorted({char for char in text if ord(char) > 127}))


    chosen = codec or min(AUTO_ORDER, key=lambda page: sum(not _fits(char, page) for char in letters))
    missing = [char for char in letters if not _fits(char, chosen)]
    escaped = sum(text.count(char) for char in missing)
    body = text.encode(chosen, errors="ai_agent_dxf_unicode")
    body, found = _CODEPAGE_LINE.subn(lambda m: m.group(1) + CODE_PAGES[chosen].encode("ascii"), body, count=1)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    released = _release_layers_at_path(path, skip_ids={layer.id()})
    staging = _staging_path(path)
    try:
        with open(staging, "wb") as handle:
            handle.write(body)
    except OSError as exc:
        _discard_staged_write(staging)
        return tool_error(f"Could not write {path}: {exc}", "EXECUTION_FAILED",
                          "Check the folder is writable, or write to another folder.")
    publish_error = _publish_staged_write(staging, path)
    if publish_error:
        _discard_staged_write(staging)
        return tool_error(f"The DXF was written but could not be put in place at {path}: {publish_error}",
                          "EXECUTION_FAILED", "Close the drawing in any CAD program holding it, or write another name.")

    out = {"exported": path, "format": "DXF", "feature_count": count, "selected_only": selected_only,
           "crs": crs.authid() or crs.description(), "encoding": chosen, "code_page": CODE_PAGES[chosen],
           "encoding_chosen": "asked" if codec else "first code page holding every string",
           "symbology_scale": round(scale), "polygons_as_lines": polygons_as_lines,
           "has_z": QgsWkbTypes.hasZ(layer.wkbType()), "labels_enabled": bool(layer.labelsEnabled()),
           "bytes": len(body)}
    if not found:
        out["warning"] = "The DXF header had no code page line, so readers will assume their own."
    if escaped:
        out["escaped_characters"] = escaped
        out["note"] = (f"{escaped} characters are not in {chosen}; they are written as AutoCAD \\U+ escapes, "
                       f"which AutoCAD and GDAL read back as the character.")
    if field:
        out["cad_layer_field"] = layer.fields().at(field_index).name()
    if crs.isGeographic():
        out["warning"] = (f"The drawing is in {crs.authid()}, degrees: CAD reads the numbers as drawing units. "
                          f"Export again with crs set to the projected CRS of the site (a UTM zone or the "
                          f"national grid) for a drawing in metres.")
    out.update(_readback(path))
    if not out.get("labels_in_file") and not layer.labelsEnabled():
        out["labels_note"] = ("The layer has no labels switched on, so the drawing carries no text. Label it "
                              "with set_layer_labels first and export again to get the lot numbers as CAD text.")
    if released:
        out["replaced_layers"] = released
    return out


def _fits(text: str, page: str) -> bool:
    try:
        text.encode(page)
    except UnicodeEncodeError:
        return False
    return True
