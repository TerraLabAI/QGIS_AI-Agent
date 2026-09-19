# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import math
import re

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsRaster,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import QVAR_DOUBLE, QVAR_LONGLONG
from .layer_lookup import _find_layer


def register_raster_data_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="set_raster_nodata",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Set NoData on {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "band": {"type": "integer", "minimum": 1},
                "value": {"type": "number"},
            },
            "required": ["layer_name", "value"],
            "additionalProperties": False,
        },
        handler=_set_raster_nodata,
    ))
    registry.register(Tool(
        name="sample_rasters_at_points",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Sample several rasters at points"),
        input_schema={
            "type": "object",
            "properties": {
                "point_layer": {"type": "string"},
                "raster_layers": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 32},
                "output_name": {"type": "string"},
                "field_prefix": {"type": "string"},
            },
            "required": ["point_layer", "raster_layers"],
            "additionalProperties": False,
        },
        handler=_sample_rasters_at_points,
    ))


def _set_raster_nodata(args: dict) -> dict:
    layer = _find_layer(str(args.get("layer_name") or ""))
    if layer is None:
        return tool_error(f"Layer {args.get('layer_name')!r} was not found.", "LAYER_NOT_FOUND")
    if not isinstance(layer, QgsRasterLayer):
        return tool_error("set_raster_nodata needs a raster layer.", "INVALID_ARGS")
    try:
        band = int(args.get("band") or 1)
        value = float(args["value"])
    except (KeyError, TypeError, ValueError):
        return tool_error("value must be a finite number and band must be a positive integer.", "INVALID_ARGS")
    if not math.isfinite(value):
        return tool_error("value must be finite.", "INVALID_ARGS")
    if band < 1 or band > layer.bandCount():
        return tool_error(f"Band {band} is outside 1..{layer.bandCount()}.", "INVALID_ARGS")
    provider = layer.dataProvider()
    setter = getattr(provider, "setNoDataValue", None)
    if setter is None:
        return tool_error("This raster provider cannot persist a NoData value.", "UNSUPPORTED",
                          "Export a writable raster with a NoData option, then retry on that layer.")
    try:
        changed = setter(band, value)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"QGIS could not set NoData on band {band}: {exc}", "WRITE_FAILED")
    if changed is False:
        return tool_error("The raster provider refused the NoData value.", "WRITE_FAILED",
                          "Use a writable GeoTIFF or another provider that supports raster metadata writes.")
    layer.triggerRepaint()
    declared = provider.sourceNoDataValue(band) if provider.sourceHasNoDataValue(band) else None
    return {"layer_name": layer.name(), "layer_id": layer.id(), "band": band,
            "nodata": declared if declared is not None else value, "persisted": True}


def _field_name(name: str, used: set[str], prefix: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", f"{prefix}{name}").strip("_").lower() or "raster_value"
    if base[0].isdigit():
        base = f"r_{base}"
    base = base[:63]
    candidate = base
    index = 2
    while candidate.casefold() in used:
        suffix = f"_{index}"
        candidate = f"{base[:63-len(suffix)]}{suffix}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def _sample_rasters_at_points(args: dict) -> dict:
    points = _find_layer(str(args.get("point_layer") or ""))
    if points is None:
        return tool_error(f"Layer {args.get('point_layer')!r} was not found.", "LAYER_NOT_FOUND")
    if not isinstance(points, QgsVectorLayer) or points.geometryType() != 0:
        return tool_error("point_layer must be a point vector layer.", "INVALID_ARGS")
    names = args.get("raster_layers")
    if not isinstance(names, list) or not names:
        return tool_error("raster_layers must contain at least one raster layer.", "INVALID_ARGS")
    rasters = []
    for requested in names:
        layer = _find_layer(str(requested or ""))
        if not isinstance(layer, QgsRasterLayer):
            return tool_error(f"{requested!r} is not a loaded raster layer.", "INVALID_ARGS")
        rasters.append(layer)
    output_name = str(args.get("output_name") or f"{points.name()} raster samples")[:120]
    output = QgsVectorLayer(f"Point?crs={points.crs().authid()}", output_name, "memory")
    if not output.isValid():
        return tool_error("QGIS could not create the point output layer.", "WRITE_FAILED")
    output.dataProvider().addAttributes([QgsField("source_fid", QVAR_LONGLONG)])
    used = {"source_fid"}
    for source_field in points.fields():
        copied_name = _field_name(source_field.name(), used, "")
        copied_field = QgsField(copied_name, source_field.type(), source_field.typeName(),
                                source_field.length(), source_field.precision(), source_field.comment())
        output.dataProvider().addAttributes([copied_field])
    fields = []
    for raster in rasters:
        field = _field_name(raster.name(), used, str(args.get("field_prefix") or ""))
        output.dataProvider().addAttributes([QgsField(field, QVAR_DOUBLE)])
        fields.append(field)
    output.updateFields()



    transforms = {
        raster.id(): (QgsCoordinateTransform(points.crs(), raster.crs(), QgsProject.instance())
                      if points.crs() != raster.crs() else None)
        for raster in rasters
    }
    features = []
    for feature in points.getFeatures():
        if feature.geometry() is None or feature.geometry().isEmpty():
            continue
        point = feature.geometry().asPoint()
        values = [feature.id()] + [feature.attribute(index) for index in range(points.fields().count())]
        for raster in rasters:
            sample_point = point
            transform = transforms.get(raster.id())
            if transform is not None:
                sample_point = transform.transform(point)
            result = raster.dataProvider().identify(
                sample_point, enum_member(QgsRaster, "IdentifyFormat", "IdentifyFormatValue"))
            identified = result.results() if result.isValid() else {}
            value = identified.get(1)
            try:
                value = float(value) if value is not None else None
            except (TypeError, ValueError):
                value = None
            values.append(value)
        out_feature = QgsFeature(output.fields())
        out_feature.setGeometry(QgsGeometry.fromPointXY(point))
        out_feature.setAttributes(values)
        features.append(out_feature)
    output.dataProvider().addFeatures(features)
    QgsProject.instance().addMapLayer(output)
    return {
        "layer_name": output.name(),
        "layer_id": output.id(),
        "source_layer": points.name(),
        "rasters": [r.name() for r in rasters],
        "fields": fields,
        "sampled_features": len(features),
        "method": "provider_identify",
        "native_algorithm_note": "native:rastersampling handles one raster per call; this folded tool "
                                 "samples all requested rasters in one call.",
    }


__all__ = ["register_raster_data_tools"]
