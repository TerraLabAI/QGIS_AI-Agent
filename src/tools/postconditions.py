# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsProject,
    QgsVectorLayer,
)

from ..core import tuning
from ..core.geometry_budget import VertexBudget
from ._layers import loaded_feature_count
from .layer_lookup import _find_layer



_GEOMETRY_SAMPLE = 1000



_KEY_SAMPLE = 5



JOIN_WARN_RATIO = 0.8


class _Coded(str):









    finding: dict

    def __new__(cls, text: str, code: str, **facts):
        made = super().__new__(cls, text)
        made.finding = {"code": code, **facts}
        return made



_JOIN_ALGORITHMS = frozenset({
    "native:joinattributestable", "qgis:joinattributestable",
    "native:joinattributesbylocation", "qgis:joinattributesbylocation",
    "native:joinbylocationsummary", "qgis:joinbylocationsummary",
    "native:joinbynearest",
})


_OVERLAY_ALGORITHMS = frozenset({
    "native:clip", "qgis:clip",
    "native:intersection", "qgis:intersection",
    "native:difference", "qgis:difference",
    "native:symmetricaldifference",
    "native:extractbylocation", "qgis:extractbylocation",
    "native:joinattributesbylocation", "qgis:joinattributesbylocation",
    "native:joinbylocationsummary",
    "native:splitwithlines", "native:clipvectorbyextent",
    "gdal:cliprasterbymasklayer",
})

_OVERLAY_PARAMS = ("OVERLAY", "CLIP", "JOIN", "INTERSECT", "MASK", "LINES")

_REDUCES_ROWS = frozenset({
    "native:dissolve", "qgis:dissolve", "native:aggregate", "native:collect",
    "native:deleteduplicategeometries", "native:removeduplicatesbyattribute",
})
_PRESERVES_ROWS = frozenset({
    "native:fieldcalculator", "qgis:fieldcalculator", "native:refactorfields",
    "native:reprojectlayer", "qgis:reprojectlayer", "native:buffer",
    "native:centroids", "native:assignprojection", "native:setzvalue",
    "native:setmvalue", "native:addfieldtoattributestable", "native:renametablefield",
})



_NEVER_GROWS = frozenset({
    "native:clip", "qgis:clip", "native:clipvectorbyextent",
    "native:extractbylocation", "qgis:extractbylocation",
    "native:extractbyattribute", "qgis:extractbyattribute",
    "native:extractbyexpression", "qgis:extractbyexpression",
    "native:randomextract",
})




_FIELD_TO_A_COPY = frozenset({
    "native:fieldcalculator", "qgis:fieldcalculator",
    "native:addfieldtoattributestable", "qgis:addfieldtoattributestable",
    "native:refactorfields",
})










_RASTER_PRESERVES = frozenset({
    "gdal:cliprasterbymasklayer", "gdal:cliprasterbyextent", "gdal:translate",
    "gdal:warpreproject", "gdal:buildvirtualraster", "gdal:merge",
    "native:cliprasterbyextent", "native:fillnodata",
})


_RASTER_MASKED = frozenset({"gdal:cliprasterbymasklayer"})

_RESOLUTION_PARAMS = ("TR", "TARGET_RESOLUTION", "RESOLUTION", "XRES", "YRES", "CELL_SIZE", "PIXEL_SIZE")


_RASTER_SAMPLE_PIXELS = 250_000


_PIXEL_SIZE_TOLERANCE = 0.01










def _algs(key: str, shipped: frozenset) -> frozenset:
    return tuning.check_algs(key, shipped)


def _selection_source(value) -> str | None:


    if not getattr(value, "selectedFeaturesOnly", False) or getattr(value, "filterExpression", ""):
        return None
    source = getattr(value, "source", None)
    try:
        return str(source.staticValue())
    except (AttributeError, RuntimeError, TypeError):
        return None


def _layer_of(value):


    if value is None:
        return None
    if hasattr(value, "crs") and hasattr(value, "extent"):
        return value
    selected_from = _selection_source(value)
    if selected_from is not None:
        return QgsProject.instance().mapLayer(selected_from)
    if isinstance(value, str):
        try:
            if "/" in value or "\\" in value or "|" in value:
                return _layer_reading(value)
            return _find_layer(value)
        except Exception:  # nosec B110
            return None
    return None


def _layer_reading(path: str):






    from ._layers import _source_key

    wanted = _source_key(path)
    matches = [layer for layer in QgsProject.instance().mapLayers().values()
               if _source_key(layer.source()) == wanted]
    return matches[0] if len(matches) == 1 else None


_INPUT_KEYS = ("INPUT", "INPUT_LAYER", "LAYER", "SOURCE", "INPUT_VECTOR")


def _input_layer(parameters: dict):

    for key in _INPUT_KEYS:
        layer = _layer_of(parameters.get(key))
        if layer is not None:
            return layer
    return None


def _features_read(parameters: dict, layer) -> int | None:


    for key in _INPUT_KEYS:
        value = parameters.get(key)
        if value is None:
            continue
        if _selection_source(value) is not None:
            try:
                return int(layer.selectedFeatureCount())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return None
        break
    return _feature_count(layer)


def _overlay_layer(parameters: dict):
    for key in _OVERLAY_PARAMS:
        layer = _layer_of(parameters.get(key))
        if layer is not None:
            return layer
    return None


def _feature_count(layer) -> int | None:





    try:
        count = int(layer.featureCount())
    except Exception:
        return None


    return None if count < 0 else loaded_feature_count(layer, count)


def _primary_output(outputs: dict) -> tuple[str, dict] | tuple[None, None]:

    if not isinstance(outputs, dict):
        return None, None
    entry = outputs.get("OUTPUT")
    if isinstance(entry, dict) and "layer_id" in entry:
        return "OUTPUT", entry
    layers = [(key, value) for key, value in outputs.items()
              if isinstance(value, dict) and "layer_id" in value]
    if len(layers) == 1:
        return layers[0]
    return None, None


def _output_layer(entry: dict):
    if not isinstance(entry, dict):
        return None
    layer_id = entry.get("layer_id")
    if not layer_id:
        return None
    try:
        return QgsProject.instance().mapLayer(layer_id)
    except Exception:
        return None


def _count_output(value) -> int | None:

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        for key in ("value", "feature_count"):
            inner = _count_output(value.get(key))
            if inner is not None:
                return inner
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _key_sample(layer, field_name) -> list[str]:












    if layer is None or not isinstance(field_name, str) or not field_name:
        return []
    try:
        index = layer.fields().indexOf(field_name)
        if index < 0:
            return []
        values = layer.uniqueValues(index, limit=_KEY_SAMPLE)
    except Exception:
        return []
    return sorted(str(value) for value in list(values)[:_KEY_SAMPLE])


def _join_checks(algorithm_id: str, parameters: dict, outputs: dict,
                 input_layer, output_layer) -> tuple[dict | None, list[str]]:

    if algorithm_id not in _algs("join_algorithms", _JOIN_ALGORITHMS):
        return None, []
    matched = _count_output((outputs or {}).get("JOINED_COUNT"))
    unmatched = _count_output((outputs or {}).get("UNJOINABLE_COUNT"))
    features_in = _feature_count(input_layer) if input_layer is not None else None
    if matched is None and unmatched is not None and features_in is not None:
        matched = max(0, features_in - unmatched)
    if unmatched is None and matched is not None and features_in is not None:
        unmatched = max(0, features_in - matched)
    if matched is None and unmatched is None:


        features_out = _feature_count(output_layer) if output_layer is not None else None
        if features_in is None or features_out is None:
            return None, []
        matched, unmatched = min(features_out, features_in), max(0, features_in - features_out)

    report: dict = {"matched": matched, "unmatched": unmatched}
    total = (matched or 0) + (unmatched or 0)
    ratio = (matched / total) if total else None
    if ratio is not None:
        report["match_ratio"] = round(ratio, 4)

    warnings: list[str] = []
    if total and ratio is not None and ratio < JOIN_WARN_RATIO:
        join_layer = _layer_of(parameters.get("INPUT_2") or parameters.get("JOIN"))
        left_field = parameters.get("FIELD") or parameters.get("FIELD_1")
        right_field = parameters.get("FIELD_2")
        left = _key_sample(input_layer, left_field)
        right = _key_sample(join_layer, right_field)
        if left:
            report["input_key_sample"] = left
        if right:
            report["join_key_sample"] = right
        detail = ""
        if left and right:
            detail = (f" The keys do not look alike: {left_field} holds {', '.join(left)}"
                      f" and {right_field} holds {', '.join(right)}.")
        warnings.append(_Coded(
            f"the join matched {matched} of {total} rows ({ratio:.0%}).{detail}",
            "join_low_match", matched=matched, total=total, ratio=ratio, variant="keys" if detail else "",
            input_field=left_field, join_field=right_field, input_keys=left, join_keys=right))
    return report, warnings


def _extent_intersects(input_layer, overlay_layer) -> bool | None:





    if input_layer is None or overlay_layer is None:
        return None
    try:
        first = input_layer.extent()
        second = overlay_layer.extent()
        if first.isEmpty() or second.isEmpty():
            return None
        source_crs, target_crs = overlay_layer.crs(), input_layer.crs()
        if source_crs.isValid() and target_crs.isValid() and source_crs != target_crs:
            transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
            second = transform.transformBoundingBox(second)
        return bool(first.intersects(second))
    except Exception:
        return None





_MASK_COVERED_WARN_PCT = 95.0


def _mask_covered_pct(input_layer, mask_layer) -> float | None:




    if input_layer is None or mask_layer is None:
        return None
    try:
        raster, mask = input_layer.extent(), mask_layer.extent()
        if raster.isEmpty() or mask.isEmpty():
            return None
        source_crs, target_crs = mask_layer.crs(), input_layer.crs()
        if source_crs.isValid() and target_crs.isValid() and source_crs != target_crs:
            mask = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance()).transformBoundingBox(mask)
        area = mask.width() * mask.height()
        if area <= 0:
            return None
        shared = raster.intersect(mask)
        covered = 0.0 if shared.isEmpty() else shared.width() * shared.height()
        return round(100.0 * covered / area, 1)
    except Exception:  # nosec B110
        return None


def _invalid_geometries(layer) -> dict | None:





    if not isinstance(layer, QgsVectorLayer):
        return None




    budget = VertexBudget()
    try:
        request = QgsFeatureRequest().setLimit(_GEOMETRY_SAMPLE).setNoAttributes()
        invalid = 0
        checked = 0



        no_geometry = 0
        skipped = []
        for feature in layer.getFeatures(request):
            if budget.exhausted():
                break
            geometry = feature.geometry()
            if geometry is None or geometry.isNull() or geometry.isEmpty():
                no_geometry += 1
                continue
            if budget.oversize(geometry):
                skipped.append(feature.id())
                continue
            checked += 1
            if not geometry.isGeosValid():
                invalid += 1
    except Exception:
        return None
    if not checked and not no_geometry:
        return None
    out = {"invalid": invalid, "sampled": checked}
    if no_geometry:
        out["no_geometry"] = no_geometry
    if skipped:



        out["unchecked_large"] = len(skipped)
        out["unchecked_large_fids"] = skipped[:10]
        out["note"] = (f"{len(skipped)} geometr{'y was' if len(skipped) == 1 else 'ies were'} past the vertex cap "
                       "and not checked: validity unknown, not valid.")
    return out


def _dissolves(algorithm_id: str, parameters: dict | None) -> bool:

    return (algorithm_id in _algs("preserves_rows", _PRESERVES_ROWS)
            and str((parameters or {}).get("DISSOLVE")).strip().lower() in ("true", "1"))


def _row_count_note(algorithm_id: str, features_in: int | None, features_out: int | None,
                    parameters: dict | None = None) -> str | None:
    if features_in is None or features_out is None or not features_in:
        return None
    if algorithm_id in _algs("reduces_rows", _REDUCES_ROWS) and features_out > features_in:
        return (f"{algorithm_id.split(':')[-1]} should reduce the row count and it grew: "
                f"{features_in} in, {features_out} out.")
    if algorithm_id in _algs("never_grows", _NEVER_GROWS) and features_out > features_in:
        return (f"{algorithm_id.split(':')[-1]} cannot return more features than its input: "
                f"{features_in} in, {features_out} out.")
    if (algorithm_id in _algs("preserves_rows", _PRESERVES_ROWS)
            and not _dissolves(algorithm_id, parameters) and features_out != features_in):
        return (f"{algorithm_id.split(':')[-1]} should keep one row per input feature: "
                f"{features_in} in, {features_out} out.")
    return None


def _is_raster(layer) -> bool:


    return layer is not None and hasattr(layer, "bandCount") and hasattr(layer, "dataProvider")


def _pixel_size(layer) -> tuple[float, float] | None:
    try:
        x = float(layer.rasterUnitsPerPixelX())
        y = float(layer.rasterUnitsPerPixelY())
    except Exception:
        return None
    return (x, y) if x > 0 and y > 0 else None


def _nodata_value(layer):

    try:
        provider = layer.dataProvider()
        if not provider.sourceHasNoDataValue(1):
            return None
        return float(provider.sourceNoDataValue(1))
    except Exception:
        return None


def _alpha_band(layer) -> int | None:

    try:
        from qgis.core import QgsRaster

        from ..core.qt_compat import enum_member

        alpha = enum_member(QgsRaster, "ColorInterpretation", "AlphaBand")
        provider = layer.dataProvider()
        for band in range(1, min(int(layer.bandCount()), 64) + 1):
            if provider.colorInterpretation(band) == alpha:
                return band
    except Exception:
        return None
    return None


def _has_any_data(layer) -> bool | None:







    try:
        import warnings as _warnings

        from qgis.core import QgsRasterBandStats

        from ..core.qt_compat import enum_member

        provider = layer.dataProvider()
        with _warnings.catch_warnings():


            _warnings.simplefilter("ignore", DeprecationWarning)
            wanted = (enum_member(QgsRasterBandStats, "Stats", "Min")
                      | enum_member(QgsRasterBandStats, "Stats", "Max"))
            stats = provider.bandStatistics(1, wanted, layer.extent(), _RASTER_SAMPLE_PIXELS)
        low, high = float(stats.minimumValue), float(stats.maximumValue)
    except Exception:
        return None




    return low <= high


def _raster_checks(algorithm_id: str, parameters: dict, input_layer, output_layer) -> tuple[dict, list[str]]:

    checks: dict = {}
    warnings: list[str] = []
    if not _is_raster(output_layer):
        return checks, warnings

    try:
        bands_out = int(output_layer.bandCount())
    except Exception:
        bands_out = 0
    if bands_out:
        checks["bands_out"] = bands_out
    bands_in = None
    if _is_raster(input_layer):
        try:
            bands_in = int(input_layer.bandCount())
        except Exception:
            bands_in = None
        if bands_in:
            checks["bands_in"] = bands_in
    if (bands_in and bands_out and bands_out < bands_in
            and algorithm_id in _algs("raster_preserves", _RASTER_PRESERVES)):
        lost = f"the output has {bands_out} band(s) and the input had {bands_in}. "
        alpha_in = _alpha_band(input_layer)
        if alpha_in is not None and _alpha_band(output_layer) is None and bands_out == bands_in - 1:
            cause = "alpha"
            lost += "The source alpha band is absent; inspect transparency before accepting this result."
        elif bands_out == 1 and bands_in >= 3:
            cause = "colour"
            lost += "A colour image reduced to one band loses its RGB colours."
        else:
            cause = "other"
            lost += "Check that the remaining bands match the requested data and colour channels."
        warnings.append(_Coded(lost, "raster_bands_lost", bands_in=bands_in, bands_out=bands_out, variant=cause))

    nodata = _nodata_value(output_layer)
    checks["has_nodata_out"] = nodata is not None
    if nodata is not None:
        checks["nodata_out"] = nodata
    alpha_band = _alpha_band(output_layer)
    if alpha_band is not None:
        checks["alpha_band_out"] = alpha_band


    has_cutline = algorithm_id not in {"gdal:cliprasterbyextent", "native:cliprasterbyextent"}
    if (nodata is None and alpha_band is None and has_cutline
            and algorithm_id in _algs("raster_masked", _RASTER_MASKED)):
        warnings.append(_Coded(
            "the masked output declares neither nodata nor an alpha band. Pixels outside the cutline "
            "may count as data; inspect the mask before using output statistics.", "raster_no_mask"))

    size_out = _pixel_size(output_layer)
    if size_out:
        checks["pixel_size_out"] = [round(size_out[0], 6), round(size_out[1], 6)]
    size_in = _pixel_size(input_layer) if _is_raster(input_layer) else None
    if size_in:
        checks["pixel_size_in"] = [round(size_in[0], 6), round(size_in[1], 6)]
    asked = any(parameters.get(key) not in (None, "", 0) for key in _RESOLUTION_PARAMS)
    same_crs = False
    try:
        source_crs, output_crs = input_layer.crs(), output_layer.crs()
        same_crs = source_crs.isValid() and output_crs.isValid() and source_crs == output_crs
    except (AttributeError, RuntimeError):
        same_crs = False
    if size_in and size_out and not same_crs:
        checks["pixel_size_comparison"] = "not_compared: different or unknown CRS coordinate units"
    if (size_in and size_out and same_crs and not asked
            and algorithm_id in _algs("raster_preserves", _RASTER_PRESERVES)
            and any(abs(after - before) > _PIXEL_SIZE_TOLERANCE * before
                    for before, after in zip(size_in, size_out))):
        warnings.append(_Coded(
            f"the pixel size changed without being asked to in the same CRS: "
            f"{size_in[0]:.6g} x {size_in[1]:.6g} in, {size_out[0]:.6g} x {size_out[1]:.6g} out.",
            "raster_pixel_size_changed", size_in=list(size_in), size_out=list(size_out)))

    try:
        width, height = int(output_layer.width()), int(output_layer.height())
    except Exception:
        width = height = 0
    if width and height:
        checks["size_out"] = [width, height]

    has_data = _has_any_data(output_layer)
    if has_data is not None:
        checks["has_data"] = has_data
        if not has_data:
            warnings.append(_Coded(
                f"every one of the {_RASTER_SAMPLE_PIXELS} sampled pixels is nodata: the output is "
                "empty. Check that the two inputs cover the same ground in the same CRS.",
                "raster_all_nodata", sampled=_RASTER_SAMPLE_PIXELS))
    return checks, warnings


def _field_in_place_note(algorithm_id: str, parameters: dict, input_layer, output_layer) -> str | None:

    if algorithm_id not in _algs("field_to_a_copy", _FIELD_TO_A_COPY):
        return None
    if input_layer is None or output_layer is None or input_layer is output_layer:
        return None
    field = parameters.get("FIELD_NAME") or parameters.get("FIELD")
    field = str(field).strip() if field else ""
    try:
        before = [f.name() for f in input_layer.fields()]
        after = [f.name() for f in output_layer.fields()]
    except Exception:  # noqa: BLE001
        return None
    gained = [name for name in after if name not in before]
    if field and field in before:
        return None
    if not gained:
        return None
    named = field or gained[0]





    origin = _same_fields_layer(before, (input_layer, output_layer))
    home = origin.name() if origin is not None else input_layer.name()
    return _Coded(
        f"'{named}' is on the new layer '{output_layer.name()}'. '{home}' still has "
        f"{', '.join(before) or 'no fields'} and did not gain it. If the user asked for the field on "
        f"'{home}', write the values back onto it: add_field with the field the user named, then "
        f"update_features. Do not answer that '{home}' has the field.",
        "field_on_copy", field=named, output_layer=output_layer.name(), home_layer=home, home_fields=before)


def _same_fields_layer(names: list[str], skip: tuple):

    try:
        wanted = list(names)
        skip_ids = {layer.id() for layer in skip if layer is not None}
        for layer in QgsProject.instance().mapLayers().values():
            if layer.id() in skip_ids or not isinstance(layer, QgsVectorLayer):
                continue
            if [f.name() for f in layer.fields()] == wanted:
                return layer
    except Exception:  # noqa: BLE001
        return None
    return None


def compute_checks(algorithm_id: str, parameters: dict, outputs: dict) -> dict | None:






    try:
        algorithm_id = str(algorithm_id or "").lower()
        parameters = parameters if isinstance(parameters, dict) else {}
        input_layer = _input_layer(parameters)
        _key, entry = _primary_output(outputs)
        output_layer = _output_layer(entry)

        features_in = _features_read(parameters, input_layer) if input_layer is not None else None
        features_out = _feature_count(output_layer) if output_layer is not None else None
        if features_out is None and isinstance(entry, dict):
            count = entry.get("feature_count")
            features_out = count if isinstance(count, int) and count >= 0 else None

        checks: dict = {}
        warnings: list[str] = []

        if features_in is not None:
            checks["features_in"] = features_in
        if features_out is not None:
            checks["features_out"] = features_out
        if features_in and features_out == 0:
            warnings.append(_Coded(f"output is empty: {features_in} features in, 0 out.",
                                   "output_empty", features_in=features_in))

        if input_layer is not None and input_layer.crs().isValid():
            checks["crs_in"] = input_layer.crs().authid()
        if output_layer is not None and output_layer.crs().isValid():
            checks["crs_out"] = output_layer.crs().authid()
        if (checks.get("crs_in") and checks.get("crs_out")
                and checks["crs_in"] != checks["crs_out"]
                and "reproject" not in algorithm_id and "project" not in algorithm_id
                and not parameters.get("TARGET_CRS")):
            warnings.append(_Coded(
                f"the CRS changed without being asked to: {checks['crs_in']} in, {checks['crs_out']} out.",
                "crs_changed", crs_in=checks["crs_in"], crs_out=checks["crs_out"]))

        join_report, join_warnings = _join_checks(algorithm_id, parameters, outputs,
                                                  input_layer, output_layer)
        if join_report is not None:
            checks["join_matched"] = join_report
        warnings.extend(join_warnings)

        if algorithm_id in _algs("overlay_algorithms", _OVERLAY_ALGORITHMS):
            overlaps = _extent_intersects(input_layer, _overlay_layer(parameters))
            if overlaps is not None:
                checks["extent_intersects_input"] = overlaps
                if not overlaps:
                    warnings.append(_Coded(
                        "the two inputs do not overlap on the ground, so the result is empty by "
                        "construction. Check the CRS of both layers.", "no_overlap"))

        raster_report, raster_warnings = _raster_checks(algorithm_id, parameters,
                                                        input_layer, output_layer)
        checks.update(raster_report)
        warnings.extend(raster_warnings)
        if algorithm_id in _algs("raster_masked", _RASTER_MASKED) and _is_raster(input_layer):
            covered = _mask_covered_pct(input_layer, _overlay_layer(parameters))
            if covered is not None:
                checks["mask_covered_pct"] = covered
                if covered < _MASK_COVERED_WARN_PCT:
                    warnings.append(
                        f"the input raster covers only {covered:g}% of the mask's box, so the output holds "
                        "data over that part of the mask only. Do not call it clipped to the mask's area; if "
                        "the whole area was meant, clip a source raster that covers it.")

        geometry = _invalid_geometries(output_layer)
        if geometry is not None:
            checks["invalid_geometries"] = geometry
            if geometry["invalid"]:
                warnings.append(_Coded(
                    f"{geometry['invalid']} of {geometry['sampled']} sampled output geometries are invalid.",
                    "invalid_geometries", invalid=geometry["invalid"], sampled=geometry["sampled"]))
            if geometry.get("no_geometry"):
                checks["no_geometry"] = geometry["no_geometry"]
                warnings.append(
                    f"{geometry['no_geometry']} of the output rows read have no geometry (null or empty): they "
                    "are rows, not features on the map. Report only the rows with a geometry as made, and say "
                    "why the others got none.")

        in_place = _field_in_place_note(algorithm_id, parameters, input_layer, output_layer)
        if in_place:
            checks["field_added_to_output_only"] = True
            warnings.append(in_place)

        note = _row_count_note(algorithm_id, features_in, features_out, parameters)
        if note:
            checks["row_count_reconciled"] = False
            warnings.append(note)
        elif (algorithm_id in _algs("reduces_rows", _REDUCES_ROWS)
              or algorithm_id in _algs("preserves_rows", _PRESERVES_ROWS)
              or algorithm_id in _algs("never_grows", _NEVER_GROWS)):
            if features_in is not None and features_out is not None:
                grew = (algorithm_id in _algs("never_grows", _NEVER_GROWS)
                        and features_out > features_in)
                if not _dissolves(algorithm_id, parameters) and not grew:
                    checks["row_count_reconciled"] = True

        _invariant_checks(algorithm_id, parameters, output_layer, features_in, features_out, checks, warnings)

        if warnings:
            checks["warnings"] = [str(text) for text in warnings]
            findings = [{**text.finding, "at": at} for at, text in enumerate(warnings) if isinstance(text, _Coded)]
            if findings:
                checks["findings"] = findings
        return checks or None
    except Exception:  # nosec B110
        return None



_FIELD_CALCULATORS = frozenset({"native:fieldcalculator", "qgis:fieldcalculator"})


def _invariant_checks(algorithm_id: str, parameters: dict, output_layer, features_in, features_out,
                      checks: dict, warnings: list) -> None:








    from ..core import invariants

    note = invariants.join_rows(algorithm_id, parameters, features_in, features_out)
    if note:
        checks["join_rows_reconciled"] = False
        warnings.append(note)
    if algorithm_id in _FIELD_CALCULATORS and output_layer is not None:
        found = invariants.unit_factor(output_layer, str(parameters.get("FIELD_NAME") or "").strip())
        if found is not None:
            checks["unit_factor"] = found
            if found.get("warning"):
                warnings.append(found["warning"])
    overlay = _overlay_layer(parameters)
    clipped = invariants.clip_extent(algorithm_id, parameters, output_layer, overlay)
    if clipped is not None:
        checks["inside_clip_extent"] = clipped["inside_clip_extent"]
        if clipped.get("warning"):


            warnings.append(clipped["warning"])
            return
    if _is_raster(output_layer):
        measured = [("value_range", invariants.index_range(
            output_layer, invariants.expected_range(algorithm_id, parameters)))]
    else:
        measured = [("area_of_interest", invariants.overlay_area(algorithm_id, output_layer, overlay)),
                    ("zone_coverage", invariants.zonal_coverage(algorithm_id, parameters, output_layer))]
    for key, found in measured:
        if found is not None:
            checks[key] = found
            if found.get("warning"):
                warnings.append(found["warning"])


def report_checks(out: dict, algorithm_id: str, parameters: dict) -> None:

    checks = compute_checks(algorithm_id, parameters, out.get("outputs") or {})
    if checks:
        out["checks"] = checks
