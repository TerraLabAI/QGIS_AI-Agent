# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
































from __future__ import annotations




_FIELD_SAMPLE = 20






_SHAPES: dict[str, str] = {

    "add_vector_layer": "arrives",
    "add_data": "arrives",
    "add_cog_layer": "arrives",
    "add_xyz_layer": "arrives",
    "add_wms_layer": "arrives",
    "add_wfs_layer": "arrives",
    "add_vector_from_url": "arrives",
    "add_points_from_json": "arrives",
    "create_memory_layer": "arrives",
    "fetch_osm_data": "arrives",
    "fetch_building_footprints": "arrives",
    "fetch_overture": "arrives",
    "get_route": "arrives",
    "get_isochrone": "arrives",
    "delineate_watershed": "arrives",
    "extract_stream_network": "arrives",
    "map_drainage": "arrives",
    "terrain_visualisation": "arrives",
    "detect_terrain_anomalies": "arrives",
    "georeference_raster": "arrives",
    "save_layer_to_gpkg": "arrives",
    "import_csv": "arrives",
    "load_csv": "arrives",

    "set_layer_crs": "layer",
    "set_layer_style": "layer",
    "set_layer_symbology": "layer",
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
    except Exception:  # noqa: BLE001
        return None


def _text(value) -> str:

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("id", "layer_id", "name", "layer_name"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def _refs(args: dict, result: dict) -> tuple[list[str], bool]:

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


        if rect is None or rect.isNull():
            return None
        return [round(float(rect.xMinimum()), 6), round(float(rect.yMinimum()), 6),
                round(float(rect.xMaximum()), 6), round(float(rect.yMaximum()), 6)]
    except Exception:  # noqa: BLE001
        return None


def _field_facts(layer, field_name: str) -> tuple[dict, str]:

    facts: dict = {"field": field_name}
    try:
        fields = layer.fields()
        index = fields.indexOf(field_name)
    except Exception:  # noqa: BLE001
        return facts, ""
    if index < 0:
        facts["field_present"] = False
        return facts, (f"{field_name!r} is not on {layer.name()!r} after this call; adding it again "
                       f"returns the error.")
    facts["field_present"] = True
    try:
        facts["field_type"] = str(fields.at(index).typeName())
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    sampled, filled = _sample(layer, index)
    if sampled is None:
        return facts, ""
    facts["sampled"] = sampled
    facts["non_null"] = filled
    if sampled and not filled:
        return facts, (f"Every one of {sampled} sampled rows of {field_name!r} is empty: the expression "
                       f"wrote nothing.")
    return facts, ""


def _sample(layer, index: int) -> tuple[int | None, int]:

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
            "warning": (f"{ref!r} is not in the project after this call; list_layers shows what is "
                        "there now.")}






_SCHEMA_FIELDS = 24
_SCHEMA_NAME_CHARS = 40


def _schema(layer) -> dict:






    from . import tuning

    if not tuning.flag("results", "layer_facts", False) or not hasattr(layer, "fields"):
        return {}
    try:
        from qgis.core import QgsWkbTypes

        out: dict = {"geometry": str(QgsWkbTypes.displayString(layer.wkbType()))}
        fields = layer.fields()
        count = fields.count()
        out["fields"] = ", ".join(f"{fields.at(i).name()[:_SCHEMA_NAME_CHARS]} {fields.at(i).typeName()}"
                                  for i in range(min(count, _SCHEMA_FIELDS)))
        if count > _SCHEMA_FIELDS:
            out["fields_more"] = count - _SCHEMA_FIELDS
        return out
    except Exception:  # noqa: BLE001
        return {}


def _listed(result: dict) -> list[dict]:


    entries: list = []
    for key in ("outputs", "layers"):
        value = result.get(key)
        entries.extend(value.values() if isinstance(value, dict) else value if isinstance(value, list) else ())
    changed = result.get("changed")
    if isinstance(changed, dict) and isinstance(changed.get("added"), list):
        entries.extend(changed["added"])
    return [entry for entry in entries if isinstance(entry, dict)]


def _entry_id(entry: dict) -> str:
    return _text(entry.get("layer_id")) or _text(entry.get("id"))


def _one_layer(result: dict) -> bool:

    own = _text(result.get("layer_id"))
    return all(_entry_id(entry) in ("", own) for entry in _listed(result))


def describe(result) -> None:



    if not isinstance(result, dict) or "_error" in result:
        return
    try:
        project = _project()
        own = _text(result.get("layer_id")) if isinstance(result.get("verified"), dict) else ""
        for entry in _listed(result):
            lid = _entry_id(entry)
            if not lid or lid == own or project is None:
                continue
            layer = project.mapLayer(lid)
            if layer is None:
                continue
            facts = _schema(layer)
            if not facts:
                continue
            if "layer_id" not in entry and "crs" not in entry:
                crs = _crs(layer)
                if crs:
                    entry["crs"] = crs
                count = _feature_count(layer)
                if count is not None:
                    entry["features"] = count
            for key, value in facts.items():
                if key == "geometry" and "geometry_type" in entry:
                    continue
                entry.setdefault(key, value)
    except Exception:  # noqa: BLE001
        return


def verify(name: str, args, result) -> dict | None:





    if not isinstance(result, dict) or "_error" in result or "checks" in result:
        return None
    shape = _SHAPES.get(str(name))
    args = args if isinstance(args, dict) else {}
    if shape is None:
        return _index_range(args, result)
    try:
        return _verify(shape, args, result)
    except Exception:  # noqa: BLE001
        return None


def _verify(shape: str, args: dict, result: dict) -> dict | None:
    layer, ref, strong, shared = _resolve(args, result)
    if shape == "removed":
        if not ref or shared:
            return None
        if layer is None:
            return {"layer": ref, "present": False, "removed": True}
        return {"layer": ref, "present": True, "removed": False,
                "warning": f"{ref!r} is still in the project after remove_layer."}
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
    if shape == "arrives" and "fields" not in result and _one_layer(result):
        out.update(_schema(layer))
    warning = ""
    if count == 0:
        warning = f"{layer.name()!r} is in the project with 0 features."
    elif extent is None and count is not None:
        warning = (f"{layer.name()!r} has no extent, so nothing of it can be shown on the map.")
    if shape == "field":
        field_name = _field_name(args, result)
        if field_name:
            facts, field_warning = _field_facts(layer, field_name)
            out.update(facts)
            warning = field_warning or warning
            if facts.get("non_null") and not warning:

                from .invariants import unit_factor

                found = unit_factor(layer, field_name)
                if found is not None:
                    out["unit_factor"] = found
                    warning = found.get("warning") or ""
    if warning:
        out["warning"] = warning
    return out


def _index_range(args: dict, result: dict) -> dict | None:





    try:
        from .invariants import index_range, normalised_difference

        if not normalised_difference(args.get("expression")):
            return None
        layer, _ref, _strong, _shared = _resolve(args, result)
        found = index_range(layer, (-1.0, 1.0, "a normalised difference index")) if layer is not None else None
        if not found or not found.get("warning"):
            return None
        return {"layer": layer.name(), "present": True, "value_range": found,
                "warning": f"{layer.name()!r}: {found['warning']}"}
    except Exception:  # noqa: BLE001
        return None
