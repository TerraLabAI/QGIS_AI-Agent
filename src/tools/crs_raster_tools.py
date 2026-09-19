# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import math
import os

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import security
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _find_layer


def _crs_from_text(raw: str):
    text = str(raw or "").strip()
    if not text:
        return None
    crs = (
        QgsCoordinateReferenceSystem.fromWkt(text)
        if text.startswith(("GEOGCRS[", "PROJCRS[", "PROJCS[", "GEODCRS[", "GEOGCS[", "BOUNDCRS[", "COMPOUNDCRS["))
        else QgsCoordinateReferenceSystem(text)
    )
    if not crs.isValid():
        crs = QgsCoordinateReferenceSystem()
        crs.createFromUserInput(text)
    return crs if crs.isValid() else None


def _read_source(source: str) -> tuple[str, str] | dict:
    path = security.expand_path(str(source or "").strip())
    if not path.lower().endswith(".prj") or not os.path.isfile(path):
        return str(source or "").strip(), ""
    error = security.validate_path(path)
    if error:
        return tool_error(error, "PERMISSION_DENIED", "Pass a .prj file in an allowed project or home folder.")
    try:
        with open(path, encoding="utf-8") as handle:
            return path, handle.read().strip()
    except (OSError, UnicodeError) as exc:
        return tool_error(
            f"Could not read the .prj file: {exc}",
            "INVALID_ARGS",
            "Check that the file is a UTF-8 or ASCII ESRI WKT file.",
        )


def _crs_details(crs: QgsCoordinateReferenceSystem, source: str) -> dict:
    return {
        "source": source,
        "valid": bool(crs.isValid()),
        "authid": str(crs.authid() or ""),
        "description": str(crs.description() or ""),
        "type": "geographic" if crs.isGeographic() else "projected",
        "map_units": str(crs.mapUnits().name if hasattr(crs.mapUnits(), "name") else crs.mapUnits()),
        "wkt": str(crs.toWkt()),
        "proj": str(crs.toProj()),
    }


def _identify_crs(args: dict) -> dict:
    source = args.get("wkt") or args.get("source") or args.get("prj_path")
    if not source:
        return tool_error(
            "A WKT string or .prj path is required.",
            "INVALID_ARGS",
            "Pass wkt for raw WKT or source/prj_path for a .prj file.",
        )
    loaded = _read_source(str(source))
    if isinstance(loaded, dict):
        return loaded
    source_label, raw = loaded
    crs = _crs_from_text(raw or str(source))
    if crs is None:
        return tool_error(
            "QGIS could not identify a valid CRS from the supplied WKT or .prj file.",
            "INVALID_ARGS",
            "Pass the complete WKT definition, not a shortened name.",
        )
    result = _crs_details(crs, source_label)
    result["matched_existing_crs"] = bool(crs.authid())
    return result


def _save_custom_crs(args: dict) -> dict:
    name = str(args.get("name") or "").strip()
    raw = str(args.get("wkt") or args.get("source") or "").strip()
    if not name or not raw:
        return tool_error(
            "Both name and WKT are required.", "INVALID_ARGS", "Pass a short custom CRS name and its complete WKT."
        )
    if len(name) > 120:
        return tool_error("The custom CRS name is too long.", "INVALID_ARGS", "Use at most 120 characters.")
    crs = _crs_from_text(raw)
    if crs is None:
        return tool_error(
            "The supplied definition is not a valid QGIS CRS.", "INVALID_ARGS", "Pass a complete WKT definition."
        )


    registry = QgsApplication.coordinateReferenceSystemRegistry()
    srsid = registry.addUserCrs(crs, name)
    if int(srsid) < 0:
        return tool_error(
            "QGIS could not save the custom CRS.", "EXECUTION_FAILED", "Choose a different name and try again."
        )
    return {
        "saved": True,
        "name": name,
        "srsid": int(srsid),
        "authid": str(crs.authid() or "USER:" + str(srsid)),
        "profile_scoped": True,
        "wkt": str(crs.toWkt()),
    }


def _resolve_raster(value):
    layer = _find_layer(str(value or ""))
    if layer is not None:
        return layer
    path = security.expand_path(str(value or ""))
    if path and os.path.isfile(path):
        if security.validate_path(path):
            return None
        return QgsRasterLayer(path, os.path.basename(path), "gdal")
    return None


def _raster_facts(value):
    layer = _resolve_raster(value)
    if layer is None or not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        return tool_error(
            f"Raster not found or invalid: {value}",
            "INVALID_ARGS",
            "Pass a loaded raster layer name/id or an allowed raster file path.",
        )
    extent = layer.extent()
    return {
        "layer": layer,
        "input": str(value),
        "name": layer.name(),
        "layer_id": layer.id(),
        "crs": str(layer.crs().authid() or layer.crs().description() or layer.crs().toWkt()),
        "crs_wkt": str(layer.crs().toWkt()),
        "extent": [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()],
        "width": int(layer.width()),
        "height": int(layer.height()),
        "pixel_size": [float(layer.rasterUnitsPerPixelX()), float(layer.rasterUnitsPerPixelY())],
    }


def _close(a: float, b: float, tolerance: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=tolerance, abs_tol=tolerance)


def _extent_in(layer, crs) -> list | None:






    extent = layer.extent()
    if layer.crs() != crs:
        try:
            transform = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance().transformContext())
            extent = transform.transformBoundingBox(extent)
        except Exception:  # noqa: BLE001
            return None
    return [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]


def _overlap_pct(extent: list, reference: list) -> float:

    width = min(extent[2], reference[2]) - max(extent[0], reference[0])
    height = min(extent[3], reference[3]) - max(extent[1], reference[1])
    area = (reference[2] - reference[0]) * (reference[3] - reference[1])
    if width <= 0 or height <= 0 or area <= 0:
        return 0.0
    return round(100.0 * width * height / area, 2)


def _compare_rasters(args: dict) -> dict:
    values = args.get("rasters") or args.get("layers")
    if not isinstance(values, list) or len(values) < 2:
        return tool_error(
            "At least two rasters are required.",
            "INVALID_ARGS",
            "Pass rasters as a list of loaded layer names/ids or file paths.",
        )
    if len(values) > 100:
        return tool_error(
            "At most 100 rasters can be compared in one call.", "INVALID_ARGS", "Compare a smaller batch."
        )
    tolerance = float(args.get("tolerance", 1e-9))
    if not math.isfinite(tolerance) or tolerance < 0:
        return tool_error(
            "tolerance must be a finite non-negative number.", "INVALID_ARGS", "Use a small value such as 1e-9."
        )
    facts = []
    for value in values:
        fact = _raster_facts(value)
        if "code" in fact:
            return fact
        facts.append(fact)
    reference = facts[0]
    checks = {"crs": True, "extent": True, "pixel_size": True, "dimensions": True}
    differences = []
    for fact in facts[1:]:
        if fact["crs_wkt"] != reference["crs_wkt"]:
            checks["crs"] = False
            differences.append(
                {"raster": fact["name"], "check": "crs", "reference": reference["crs"], "actual": fact["crs"]}
            )


        extent = _extent_in(fact["layer"], reference["layer"].crs())
        fact["overlap_pct_of_reference"] = _overlap_pct(extent, reference["extent"]) if extent else None
        if extent is None or any(not _close(a, b, tolerance) for a, b in zip(extent, reference["extent"])):
            checks["extent"] = False
            difference = {"raster": fact["name"], "check": "extent", "reference": reference["extent"],
                          "actual": extent or fact["extent"],
                          "overlap_pct_of_reference": fact["overlap_pct_of_reference"]}
            if extent is not None and fact["crs_wkt"] != reference["crs_wkt"]:
                difference["actual_in"] = reference["crs"]
            differences.append(difference)
        if any(not _close(a, b, tolerance) for a, b in zip(fact["pixel_size"], reference["pixel_size"])):
            checks["pixel_size"] = False
            differences.append(
                {
                    "raster": fact["name"],
                    "check": "pixel_size",
                    "reference": reference["pixel_size"],
                    "actual": fact["pixel_size"],
                }
            )
        if (fact["width"], fact["height"]) != (reference["width"], reference["height"]):
            checks["dimensions"] = False
            differences.append(
                {
                    "raster": fact["name"],
                    "check": "dimensions",
                    "reference": [reference["width"], reference["height"]],
                    "actual": [fact["width"], fact["height"]],
                }
            )
    for fact in facts:
        fact.pop("layer", None)
    return {
        "compatible": all(checks.values()),
        "reference": reference["name"],
        "checks": checks,
        "differences": differences,
        "rasters": facts,
    }


def register_crs_raster_tools(registry: ToolRegistry):
    registry.register(
        Tool(
            name="identify_crs",
            danger="read",
            label=QT_TRANSLATE_NOOP("AIAgent", "Identify a CRS from WKT or a .prj file"),
            input_schema={
                "type": "object",
                "properties": {"wkt": {"type": "string"}, "source": {"type": "string"}, "prj_path": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=_identify_crs,
        )
    )
    registry.register(
        Tool(
            name="save_custom_crs",
            danger="write",
            label=QT_TRANSLATE_NOOP("AIAgent", "Save the custom CRS {name}"),
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}, "wkt": {"type": "string"}, "source": {"type": "string"}},
                "required": ["name", "wkt"],
                "additionalProperties": False,
            },
            handler=_save_custom_crs,
        )
    )
    registry.register(
        Tool(
            name="compare_raster_compatibility",
            danger="read",
            label=QT_TRANSLATE_NOOP("AIAgent", "Compare raster compatibility"),
            input_schema={
                "type": "object",
                "properties": {
                    "rasters": {"type": "array", "minItems": 2, "maxItems": 100, "items": {"type": "string"}},
                    "layers": {"type": "array", "minItems": 2, "maxItems": 100, "items": {"type": "string"}},
                    "tolerance": {"type": "number", "minimum": 0},
                },
                "additionalProperties": False,
            },
            handler=_compare_rasters,
        )
    )
