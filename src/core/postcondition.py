# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a modifying call actually left in the project, measured after it ran."""































from __future__ import annotations




_FIELD_SAMPLE = 20





_SHAPES: dict[str, str] = {

    "add_vector_layer": "layer",
    "add_raster_layer": "layer",
    "add_data": "layer",
    "add_cog_layer": "layer",
    "add_xyz_layer": "layer",
    "add_wms_layer": "layer",
    "add_wfs_layer": "layer",
    "add_vector_from_url": "layer",
    "add_points_from_json": "layer",
    "create_memory_layer": "layer",
    "fetch_osm_data": "layer",
    "fetch_building_footprints": "layer",
    "fetch_overture": "layer",
    "get_route": "layer",
    "get_isochrone": "layer",
    "delineate_watershed": "layer",
    "save_layer_to_gpkg": "layer",
    "import_csv": "layer",
    "load_csv": "layer",

    "set_layer_crs": "layer",
    "set_layer_style": "layer",
    "set_layer_labels": "layer",
    "add_features": "layer",
    "update_feature_geometry": "layer",
    "select_features": "layer",
    "select_by_attribute": "layer",
    "select_by_geometry": "layer",
    "add_table_join": "layer",
    "qgis_edit_commit": "layer",
    "repair_layer_paths": "layer",

    "add_field": "field",
    "field_calculator": "field",
    "rename_field": "field",
    "update_features": "field",
    "encode_cells": "field",

    "remove_layer": "removed",
    "delete_features": "layer",
}




_RESULT_KEYS = ("output_layer", "layer_id", "layer_name", "layer", "output_name", "name")
_ARG_KEYS = ("layer_name", "layer", "layer_id", "target_layer", "output_name", "name", "INPUT", "input")




_STRONG_KEYS = frozenset({"output_layer", "layer_id", "layer_name", "layer", "target_layer", "INPUT", "input"})

_FIELD_KEYS = ("field", "field_name", "new_name", "column", "attribute")


def _project():
    try:
        from qgis.core import QgsProject

        return QgsProject.instance()
    except Exception:  # noqa: BLE001 - outside QGIS there is nothing to verify
        return None


def _text(value) -> str:
    """The layer reference inside a result value, whatever shape it came in."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("id", "layer_id", "name", "layer_name"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def _refs(args: dict, result: dict) -> tuple[list[str], bool]:
    """The layer references this call names, and whether any came from a strong key."""
    out: list[str] = []
    strong = False
    for source, keys in ((result, _RESULT_KEYS), (args, _ARG_KEYS)):
        if not isinstance(source, dict):
            continue
        for key in keys:
            ref = _text(source.get(key))
            if not ref:
                continue
            if key in _STRONG_KEYS:
                strong = True
            if ref not in out:
                out.append(ref)
    return out, strong


def _find(ref: str):
    """The layer by id, then by exact name."""

    project = _project()
    if project is None or not ref:
        return None
    try:
        layer = project.mapLayer(ref)
        if layer is not None:
            return layer
        matches = project.mapLayersByName(ref)
    except Exception:  # noqa: BLE001
        return None
    return matches[0] if len(matches) == 1 else None


def _resolve(args: dict, result: dict):
    refs, strong = _refs(args, result)
    for ref in refs:
        layer = _find(ref)
        if layer is not None:
            return layer, ref, strong
    return None, (refs or [""])[0], strong


def _feature_count(layer) -> int | None:
    """The provider's count, or None when it is unknown rather than zero."""
    if not hasattr(layer, "featureCount"):
        return None
    try:
        count = int(layer.featureCount())
    except Exception:  # noqa: BLE001
        return None
    return None if count < 0 else count


def _crs(layer) -> str:
    try:
        return str(layer.crs().authid() or "")
    except Exception:  # noqa: BLE001
        return ""


def _extent(layer) -> list[float] | None:
    try:
        rect = layer.extent()
        if rect is None or rect.isEmpty():
            return None
        return [round(float(rect.xMinimum()), 6), round(float(rect.yMinimum()), 6),
                round(float(rect.xMaximum()), 6), round(float(rect.yMaximum()), 6)]
    except Exception:  # noqa: BLE001
        return None


def _field_facts(layer, field_name: str) -> tuple[dict, str]:
    """The column exists, its type, and how many of a sample of rows carry a value."""
    facts: dict = {"field": field_name}
    try:
        fields = layer.fields()
        index = fields.indexOf(field_name)
    except Exception:  # noqa: BLE001
        return facts, ""
    if index < 0:
        facts["field_present"] = False
        return facts, (f"Add the column again and read the error it returns: {field_name!r} is not on "
                       f"{layer.name()!r} after this call.")
    facts["field_present"] = True
    try:
        facts["field_type"] = str(fields.at(index).typeName())
    except Exception:  # noqa: BLE001  # nosec B110 - the type is a nicety, the column's presence is the fact
        pass
    sampled, filled = _sample(layer, index)
    if sampled is None:
        return facts, ""
    facts["sampled"] = sampled
    facts["non_null"] = filled
    if sampled and not filled:
        return facts, (f"Fix the expression and write the column again: every one of {sampled} sampled rows "
                       f"of {field_name!r} is empty.")
    return facts, ""


def _sample(layer, index: int) -> tuple[int | None, int]:
    """(rows read, rows with a value) over at most ``_FIELD_SAMPLE`` features."""
    try:
        from qgis.core import QgsFeatureRequest

        from .qt_compat import enum_member

        request = QgsFeatureRequest()
        request.setLimit(_FIELD_SAMPLE)
        request.setSubsetOfAttributes([index])
        no_geometry = enum_member(QgsFeatureRequest, "Flag", "NoGeometry", None)
        if no_geometry is not None:
            request.setFlags(no_geometry)
        sampled = filled = 0
        for feature in layer.getFeatures(request):
            sampled += 1
            value = feature.attribute(index)
            if value is not None and str(value) not in ("", "NULL"):
                filled += 1
            if sampled >= _FIELD_SAMPLE:
                break
        return sampled, filled
    except Exception:  # noqa: BLE001
        return None, 0


def _field_name(args: dict, result: dict) -> str:
    for source in (result, args):
        if not isinstance(source, dict):
            continue
        for key in _FIELD_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _missing(ref: str) -> dict:
    return {"layer": ref, "present": False,
            "warning": (f"Call list_layers and run the step again: {ref!r} is not in the project after "
                        "this call.")}


def verify(name: str, args, result) -> dict | None:
    """The ``verified`` block for one finished modifying call, or None."""




    if not isinstance(result, dict) or "_error" in result or "checks" in result:
        return None
    shape = _SHAPES.get(str(name))
    if shape is None:
        return None
    args = args if isinstance(args, dict) else {}
    try:
        return _verify(shape, args, result)
    except Exception:  # noqa: BLE001 - a verification is never the reason a call fails
        return None


def _verify(shape: str, args: dict, result: dict) -> dict | None:
    layer, ref, strong = _resolve(args, result)
    if shape == "removed":
        if not ref:
            return None
        if layer is None:
            return {"layer": ref, "present": False, "removed": True}
        return {"layer": ref, "present": True, "removed": False,
                "warning": (f"Say so instead of reporting it gone: {ref!r} is still in the project after "
                            "remove_layer.")}
    if layer is None:
        return _missing(ref) if ref and strong else None

    out: dict = {"layer": layer.name(), "present": True}
    crs = _crs(layer)
    if crs:
        out["crs"] = crs
    count = _feature_count(layer)
    if count is not None:
        out["features"] = count
        out["empty"] = count == 0
    extent = _extent(layer)
    if extent is not None:
        out["extent"] = extent
    warning = ""
    if count == 0:
        warning = (f"Check the source before building on it: {layer.name()!r} is in the project with "
                   "0 features.")
    elif extent is None and count is not None:
        warning = (f"Check the geometries before drawing them: {layer.name()!r} has no extent, so nothing "
                   "of it can be shown on the map.")
    if shape == "field":
        field_name = _field_name(args, result)
        if field_name:
            facts, field_warning = _field_facts(layer, field_name)
            out.update(facts)
            warning = field_warning or warning
    if warning:
        out["warning"] = warning
    return out
