# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import difflib

from ..core import ground
from ..core.context_layers import crs_units
from ..core.crs_ref import crs_ref
from ..core.tool_registry import tool_error
from ._compat import is_raster, is_vector
from ._layers import layer_not_found, resolve_layer


def _find_layer(name_or_id: str):








    return resolve_layer(name_or_id)


def _layer_not_found_error(name_or_id: str) -> dict:





    return layer_not_found(name_or_id)


def require_vector(name_or_id: str, role: str = "", hint: str = "layer_not_vector"):







    return _require(name_or_id, role, is_vector, "vector", hint)


def require_raster(name_or_id: str, role: str = "", hint: str = "layer_not_raster"):

    return _require(name_or_id, role, is_raster, "raster", hint)


def _require(name_or_id, role: str, fits, wanted: str, hint: str):
    layer = _find_layer(str(name_or_id or ""))
    if layer is None:
        error = layer_not_found(str(name_or_id or ""))
        if role:
            error["_error"] = f"{role}: {error.get('_error', '')}"
        return None, error
    if not fits(layer):
        kind = type(layer).__name__.replace("Qgs", "").replace("Layer", "").lower() or "other"
        lead = f"{role} " if role else "Layer "
        return None, tool_error(f"{lead}{layer.name()!r} is a {kind} layer, not a {wanted} layer.",
                                "INVALID_ARGS", hint=hint, layer=layer.name(), kind=kind)
    return layer, None


def extent_block(extent, crs) -> dict:















    block = {"xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
             "xmax": extent.xMaximum(), "ymax": extent.yMaximum(),
             "crs": crs_ref(crs)}
    if crs is None or not crs.isValid():
        return block
    try:
        block["units"] = crs_units(crs)

        digits = 6 if crs.isGeographic() else 2
        width, height = extent.width(), extent.height()
        block["width"] = round(width, digits)
        block["height"] = round(height, digits)
        centre = extent.center()
        scale = ground.metres_per_unit(crs, centre.x(), centre.y())
    except Exception:  # noqa: BLE001
        return block
    if ground.distorted(scale):
        block["ground_width_m"] = round(width * scale, 1)
        block["ground_height_m"] = round(height * scale, 1)
    return block


def _field_not_found_error(layer, field_name: str) -> dict:

















    all_fields = [f.name() for f in layer.fields()]
    suggestions = difflib.get_close_matches(field_name, all_fields, n=3, cutoff=0.5)
    msg = f"The layer {layer.name()!r} has no field {field_name!r}."
    if suggestions:
        msg += f" Did you mean: {', '.join(repr(s) for s in suggestions)}?"
    elif all_fields:
        msg += f" Available: {', '.join(repr(n) for n in all_fields[:12])}"
    if suggestions:
        advice = f"{suggestions[0]!r} is the closest field; other fields also work."
    elif all_fields:
        advice = ("get_layer_info lists every field. "
                  "The layer exists; the field name is wrong.")
    else:
        advice = "This layer carries no attribute fields, so no field name will work on it."
    return {"_error": msg, "code": "INVALID_ARGS", "suggestion": advice,
            "_suggestions": suggestions, "fields": all_fields}


def _duplicate_layer_names(project) -> set[str]:

    seen: dict[str, int] = {}
    for layer in project.mapLayers().values():
        name = layer.name()
        seen[name] = seen.get(name, 0) + 1
    return {name for name, count in seen.items() if count > 1}


def _geometry_type_name(layer) -> str:
    geom = layer.geometryType()
    return geom.name if hasattr(geom, "name") else str(geom)


def _is_qgis_null(value) -> bool:






    try:
        from qgis.core import NULL
        if value is NULL:
            return True
        return bool(value == NULL)
    except Exception:  # nosec B110
        return False


def _jsonable_value(value):

    try:
        from qgis.core import QgsGeometry
        if isinstance(value, QgsGeometry):
            return value.asWkt(precision=6)
    except Exception:  # nosec B110
        pass
    if value is None or _is_qgis_null(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable_value(v) for v in value]
    if type(value).__name__ in ("QDate", "QDateTime", "QTime"):
        from ..core.serialization import qt_temporal_text

        return qt_temporal_text(value)
    return str(value)
