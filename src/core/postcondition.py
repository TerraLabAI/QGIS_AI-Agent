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
    "extract_stream_network": "layer",
    "map_drainage": "layer",
    "terrain_visualisation": "layer",
    "detect_terrain_anomalies": "layer",
    "georeference_raster": "layer",
    "save_layer_to_gpkg": "layer",
    "import_csv": "layer",
    "load_csv": "layer",

    "set_layer_crs": "layer",
    "set_layer_style": "layer",
    "set_layer_labels": "layer",
    "set_layer_temporal": "layer",
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
    """The layer by id, then by exact name, and how many layers answer to that name."""




    project = _project()
    if project is None or not ref:
        return None, 0
    try:
        layer = project.mapLayer(ref)
        if layer is not None:
            return layer, 1
        matches = project.mapLayersByName(ref)
    except Exception:  # noqa: BLE001
        return None, 0
    return (matches[0] if len(matches) == 1 else None), len(matches)


def _result_ids(result: dict) -> list[tuple[str, str]]:
    """(layer id, name) for every layer the result names by id: its own and each entry of the layers it lists (fetch_overture's families, a."""

    entries = [result]
    for key in ("layers", "outputs"):
        value = result.get(key)
        if isinstance(value, list):
            entries.extend(value)
        elif isinstance(value, dict):
            entries.extend(value.values())
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        lid = _text(entry.get("layer_id"))
        if lid and lid not in seen:
            seen.add(lid)
            out.append((lid, _text(entry.get("layer_name") or entry.get("name")) or lid))
    return out


def _resolve(args: dict, result: dict):
    """(layer, reference, strong, shared)."""



    ids = _result_ids(result) if isinstance(result, dict) else []
    project = _project()
    if ids and project is not None:
        for lid, _name in ids:
            try:
                layer = project.mapLayer(lid)
            except Exception:  # noqa: BLE001
                layer = None
            if layer is not None:
                return layer, lid, True, False
        return None, ids[0][1], True, False
    refs, strong = _refs(args, result)
    shared = False
    for ref in refs:
        layer, count = _find(ref)
        if layer is not None:
            return layer, ref, strong, False
        shared = shared or count > 1
    return None, (refs or [""])[0], strong, shared


def _feature_count(layer) -> int | None:
    """What the layer holds: the stamped true count for a capped remote load (``layer_order.mark_truncated_count``), else the provider's own."""




    if not hasattr(layer, "featureCount"):
        return None
    from .layer_order import feature_count_of

    return feature_count_of(layer)


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


def _added_nothing(result: dict) -> bool:
    """The result itself says no layer came of this call: a zero count and no layer id."""








    if _result_ids(result):
        return False
    if "layer_name" in result and result["layer_name"] is None:
        return True
    for key in ("feature_count", "features"):
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value == 0:
            return True
    return False


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
    layer, ref, strong, shared = _resolve(args, result)
    if shape == "removed":
        if not ref or shared:
            return None
        if layer is None:
            return {"layer": ref, "present": False, "removed": True}
        return {"layer": ref, "present": True, "removed": False,
                "warning": (f"Say so instead of reporting it gone: {ref!r} is still in the project after "
                            "remove_layer.")}
    if layer is None:
        if not ref or not strong or shared or _added_nothing(result):
            return None
        return _missing(ref)

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
