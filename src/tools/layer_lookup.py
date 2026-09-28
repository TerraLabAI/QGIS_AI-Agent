# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import difflib

from ._layers import layer_not_found, resolve_layer, resolve_layer_note


def _find_layer(name_or_id: str):








    return resolve_layer(name_or_id)


def _find_layer_note(name_or_id: str) -> tuple:






    return resolve_layer_note(name_or_id)


def _layer_not_found_error(name_or_id: str) -> dict:





    return layer_not_found(name_or_id)


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
