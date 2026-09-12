# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only inspection handlers: features, expressions, field/raster stats, renderer introspection, provider capabilities and geometry."""


from __future__ import annotations

import json
import math
import threading

from qgis.core import QgsExpression, QgsFeatureRequest, QgsGeometry, QgsProject, QgsRasterLayer, QgsVectorLayer

from ..core import ground, net
from ..core.geometry_budget import VertexBudget
from ..core.policy import MAX_LIST_CHARS
from ..core.qt_compat import enum_member
from ..core.vsi import streamed_in_place
from .data_tools import _run_on_main_thread
from .layer_lookup import _find_layer, _jsonable_value, _layer_not_found_error





STATS_CHUNK = 20_000

















_SCAN_PERMITS = threading.BoundedSemaphore(1)


_SCAN_WAIT_S = 240.0


def _get_features(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a vector layer"}

    limit = max(int(args.get("limit", 100) or 100), 1)
    offset = max(int(args.get("offset", 0) or 0), 0)
    include_geometry = bool(args.get("include_geometry", False))

    available = {f.name() for f in layer.fields()}
    names = [f.name() for f in layer.fields()]
    fields_arg = args.get("fields")
    if isinstance(fields_arg, list) and fields_arg:
        selected = [f for f in fields_arg if f in available]
        if not selected:
            return {"_error": f"None of the requested fields exist: {fields_arg}", "fields": names}
        names = selected

    request = QgsFeatureRequest()
    expression = args.get("expression")







    if expression and streamed_in_place(layer):
        return {
            "_error": (f"'{layer.name()}' is read over HTTP where it is published, so filtering it "
                       "scans the whole file across the network instead of reading its index."),
            "_code": "INVALID_ARGS",
            "_suggestion": ("Clip it to the area first, then filter the local copy. "
                            "Reading a page without an expression stays fast."),
        }
    if expression:
        from qgis.core import QgsExpressionContext, QgsExpressionContextUtils
        expr = QgsExpression(expression)
        if expr.hasParserError():
            return {"_error": f"Invalid expression: {expr.parserErrorString()}", "_code": "INVALID_ARGS"}
        request.setFilterExpression(expression)





        ctx = QgsExpressionContext()
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        request.setExpressionContext(ctx)


    native_offset = hasattr(request, "setOffset")
    if native_offset:
        request.setOffset(offset)
        request.setLimit(limit + 1)
    else:
        request.setLimit(offset + limit + 1)

    collected = []
    size = 0
    over_budget = False
    for i, feat in enumerate(layer.getFeatures(request)):
        if not native_offset and i < offset:
            continue
        row = {"_fid": feat.id()}
        for name in names:
            row[name] = feat[name]
        if include_geometry:
            geom = feat.geometry()
            row["_wkt"] = None if (geom is None or geom.isNull()) else geom.asWkt(6)




        size += len(json.dumps(row, default=str))
        if collected and size > MAX_LIST_CHARS:
            over_budget = True
            break
        collected.append(row)

    fetched = len(collected)
    features = collected[:limit]
    result = {"layer": layer.name(), "count": len(features), "offset": offset, "features": features}
    if include_geometry:
        result["geometry_crs"] = layer.crs().authid()
    if over_budget or fetched > limit:
        result["next_offset"] = offset + len(features)
        result["more_available"] = True
    if over_budget:
        result["page_limited_by"] = (
            f"{MAX_LIST_CHARS} characters. Ask for fewer fields, a filter or "
            "the next page with offset."
        )
    return result


def _evaluate_expression(args: dict) -> dict:
    from qgis.core import QgsExpressionContext, QgsExpressionContextUtils

    expression = args.get("expression")
    if not expression:
        return {"_error": "expression is required"}
    expr = QgsExpression(expression)
    if expr.hasParserError():
        return {"_error": f"Expression parser error: {expr.parserErrorString()}", "_code": "INVALID_ARGS"}

    ctx = QgsExpressionContext()
    layer = None
    layer_name = args.get("layer_name")
    if layer_name:
        layer = _find_layer(layer_name)
        if not layer:
            return _layer_not_found_error(layer_name)
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    else:
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(None))

    expr.prepare(ctx)
    feature_id = args.get("feature_id")
    is_vector = isinstance(layer, QgsVectorLayer)
    needs_feature = expr.needsGeometry() or bool(expr.referencedColumns())



    if is_vector and feature_id is not None:
        feat = layer.getFeature(int(feature_id))
        if not feat.isValid():




            if int(feature_id) <= 0 and not needs_feature:
                value = expr.evaluate(ctx)
                if expr.hasEvalError():
                    return {"_error": f"Expression evaluation error: {expr.evalErrorString()}", "_code": "INVALID_ARGS"}
                return {"expression": expression, "layer": layer.name(), "result": _jsonable_value(value),
                        "note": "evaluated in the layer scope, no feature: an aggregate over the whole layer"}
            first = next(layer.getFeatures(), None)
            hint = f"; ids start at {first.id()}" if first is not None and first.isValid() else ""
            return {"_error": f"No feature with id {feature_id} in '{layer.name()}'{hint}", "_code": "INVALID_ARGS",
                    "_suggestion": "Leave feature_id out for an aggregate over the layer, or pass an id from "
                                   "get_features."}
        ctx.setFeature(feat)
        value = expr.evaluate(ctx)
        if expr.hasEvalError():
            return {"_error": f"Expression evaluation error: {expr.evalErrorString()}", "_code": "INVALID_ARGS"}
        return {"expression": expression, "feature_id": int(feature_id), "result": _jsonable_value(value)}

    if is_vector and needs_feature:
        from qgis.core import QgsFeatureRequest
        limit = args.get("limit", 10)
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit = 10
        results = []
        for feat in layer.getFeatures(QgsFeatureRequest().setLimit(limit)):
            ctx.setFeature(feat)
            value = expr.evaluate(ctx)
            if expr.hasEvalError():
                return {
                    "_error": f"Expression evaluation error on feature {feat.id()}: {expr.evalErrorString()}",
                    "_code": "INVALID_ARGS",
                }
            results.append({"feature_id": feat.id(), "result": _jsonable_value(value)})
        return {
            "expression": expression,
            "per_feature": True,
            "evaluated": len(results),
            "feature_count": layer.featureCount(),
            "results": results,
            "_note": "Expression references geometry/fields → evaluated per feature. Pass feature_id for one feature, "
            "or wrap in an aggregate (sum/mean/count) for a single layer-wide value.",
        }


    value = expr.evaluate(ctx)
    if expr.hasEvalError():
        return {"_error": f"Expression evaluation error: {expr.evalErrorString()}", "_code": "INVALID_ARGS"}
    out = {"expression": expression, "result": _jsonable_value(value)}
    if value is None and needs_feature and not is_vector:
        out["_note"] = (
            "Result is null because the expression needs a feature but no vector layer_name was given. Pass layer_name "
            "(and optionally feature_id)."
        )
    return out


def _get_field_statistics(args: dict) -> dict:
    """Every statistic of one field, from one pass read a slice at a time."""
















    if not _SCAN_PERMITS.acquire(timeout=_SCAN_WAIT_S):
        return {"_error": ("Another field is still being scanned; this one waited "
                           f"{_SCAN_WAIT_S:.0f} seconds for its turn."),
                "_code": "INVALID_ARGS",
                "_suggestion": "Ask for one field at a time, or read the column with get_features."}
    try:
        return _scan_field(args)
    finally:
        _SCAN_PERMITS.release()


def _scan_field(args: dict) -> dict:
    """One scan, holding one provider iterator, under the permit taken above."""
    state = _run_on_main_thread(_stats_open, args["layer_name"], args.get("field"), timeout=60)
    if state.get("_error"):
        return state
    try:
        while True:
            cancelled = net.current_cancel_check()
            try:
                stopped = callable(cancelled) and bool(cancelled())
            except Exception:  # noqa: BLE001 - a broken optional cancel check does not fail a read
                stopped = False
            if stopped:
                return {"_error": "The run was stopped.", "_code": "CANCELLED",
                        "_suggestion": "Wait for the next user message."}
            if _run_on_main_thread(_stats_read, state, timeout=120):
                break
    finally:
        _run_on_main_thread(_stats_close, state, timeout=30)
    return _stats_summary(state)


def _stats_open(layer_name: str, field: str) -> dict:
    """Main thread: the layer, the field, and the iterator the slices advance."""
    layer = _find_layer(layer_name)
    if not layer:
        return _layer_not_found_error(layer_name)
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{layer_name}' is not a vector layer"}
    if not field:
        return {"_error": "field is required"}
    index = layer.fields().indexOf(field)
    if index < 0:
        return {"_error": f"Field not found: {field}", "fields": [f.name() for f in layer.fields()]}
    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setSubsetOfAttributes([index])
    return {"layer_name": layer.name(), "field": field, "index": index,
            "features": layer.getFeatures(request), "numbers": [], "others": [],
            "total": 0, "missing": 0, "numeric_only": True}


def _stats_read(state: dict) -> bool:
    """Main thread: fold at most ``STATS_CHUNK`` rows in."""





    numbers, others, index = state["numbers"], state["others"], state["index"]
    for read, feature in enumerate(state["features"], start=1):
        state["total"] += 1
        value = feature[index]
        if value is None or (hasattr(value, "isNull") and value.isNull()):
            state["missing"] += 1
        elif state["numeric_only"] and not isinstance(value, bool) and isinstance(value, (int, float)):
            numbers.append(float(value))
        else:
            state["numeric_only"] = False
            others.append(value)
        if read >= STATS_CHUNK:
            return False
    return True


def _stats_close(state: dict) -> None:
    """Main thread: the provider's iterator is released where it was opened."""
    features = state.pop("features", None)
    if features is not None:
        try:
            features.close()
        except Exception:  # nosec B110 - an iterator already spent needs no closing
            pass


def _stats_summary(state: dict) -> dict:
    """The summaries themselves, in the worker."""








    from qgis.core import QgsStatisticalSummary, QgsStringStatisticalSummary

    name, field = state["layer_name"], state["field"]
    numbers, others, missing = state["numbers"], state["others"], state["missing"]
    stats: dict = {"count": state["total"]}
    if state["numeric_only"] and numbers:
        summary = QgsStatisticalSummary()
        summary.calculate(numbers)

        stats["count_distinct"] = summary.variety() + (1 if missing else 0)
        stats["count_missing"] = missing
        for key, value in (("min", summary.min()), ("max", summary.max()), ("sum", summary.sum()),
                           ("mean", summary.mean()), ("median", summary.median()),
                           ("stdev", summary.stDev()), ("q1", summary.firstQuartile()),
                           ("q3", summary.thirdQuartile()), ("iqr", summary.interQuartileRange())):
            stats[key] = _jsonable_value(value)
        return _statistics_result(name, field, stats)

    values = [str(value) for value in others + numbers]
    if values or missing:
        summary = QgsStringStatisticalSummary()
        summary.calculate(values + [None] * missing)
        stats["count_distinct"] = summary.countDistinct()
        stats["count_missing"] = summary.countMissing()
        if values:
            stats["min"] = _jsonable_value(summary.min())
            stats["max"] = _jsonable_value(summary.max())
    else:
        stats["count_distinct"] = 0
        stats["count_missing"] = missing
    return _statistics_result(name, field, stats)


def _statistics_result(layer_name: str, field: str, stats: dict) -> dict:


    order = ("count", "count_distinct", "count_missing", "min", "max", "sum", "mean",
             "median", "stdev", "q1", "q3", "iqr")
    ordered = {key: stats[key] for key in order if key in stats}
    return {"layer": layer_name, "field": field, "statistics": ordered}


def _get_renderer_info(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a vector layer"}

    renderer = layer.renderer()
    if renderer is None:
        return {"layer": layer.name(), "renderer": None}

    def _color(symbol):
        try:
            return symbol.color().name() if symbol else None
        except Exception:  # nosec B112 - unsupported item is omitted
            return None

    rtype = renderer.type()
    info = {"type": rtype, "opacity": layer.opacity()}
    omitted = 0
    try:
        if rtype == "categorizedSymbol":
            info["field"] = renderer.classAttribute()
            categories = renderer.categories()
            info["categories"] = [
                {"value": _jsonable_value(c.value()), "label": c.label(), "color": _color(c.symbol())}
                for c in categories[:5000]
            ]
            omitted = max(0, len(categories) - 5000)
        elif rtype == "graduatedSymbol":
            info["field"] = renderer.classAttribute()
            ranges = renderer.ranges()
            info["ranges"] = [
                {"lower": r.lowerValue(), "upper": r.upperValue(), "label": r.label(), "color": _color(r.symbol())}
                for r in ranges[:5000]
            ]
            omitted = max(0, len(ranges) - 5000)
        elif rtype == "singleSymbol":
            info["color"] = _color(renderer.symbol())
    except Exception as e:
        info["_warning"] = f"Partial renderer introspection: {e}"
    if omitted:
        info["classes_omitted"] = omitted
    return {"layer": layer.name(), "renderer": info}


def _raster_sample(args: dict) -> dict:
    from qgis.core import QgsCoordinateTransform, QgsPointXY

    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsRasterLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a raster layer"}





    provider_type = layer.providerType()
    if provider_type == "wms":
        return {
            "_error": (
                f"Layer '{layer.name()}' is a WMS/WMTS/XYZ tile layer and has no sampleable pixel "
                "values (it serves rendered image tiles, not numeric bands)."
            ),
            "_code": "INVALID_ARGS",
            "provider_type": provider_type,
        }

    point = QgsPointXY(float(args["x"]), float(args["y"]))
    crs_id = args.get("crs")
    if crs_id:
        from qgis.core import QgsCoordinateReferenceSystem
        src = QgsCoordinateReferenceSystem(crs_id)
        if not src.isValid():
            return {"_error": f"Invalid coordinate CRS: {crs_id}", "_code": "INVALID_ARGS",
                    "_suggestion": "Pass an EPSG code such as EPSG:4326."}
        if src.isValid() and src != layer.crs():
            try:
                point = QgsCoordinateTransform(src, layer.crs(), QgsProject.instance()).transform(point)
            except Exception as e:
                return {"_error": f"Coordinate transform failed: {e}"}

    band = int(args.get("band", 1))
    if band < 1 or band > layer.bandCount():
        return {"_error": f"Band {band} is outside the raster's 1..{layer.bandCount()} range.",
                "_code": "INVALID_ARGS", "_suggestion": "Pass an available raster band."}
    try:
        value, ok = layer.dataProvider().sample(point, band)
    except Exception as e:
        return {"_error": f"Sampling failed: {e}"}
    if not ok:
        return {"_error": "No data at this location (outside extent or nodata)", "_code": "INVALID_ARGS"}
    return {"layer": layer.name(), "band": band, "value": value, "x": point.x(), "y": point.y()}





COVERAGE_FULL_PCT = 98.0


def _get_raster_band_stats(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsRasterLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a raster layer"}

    band = int(args.get("band", 1))
    if band < 1 or band > layer.bandCount():
        return {"_error": f"Band {band} is outside the raster's 1..{layer.bandCount()} range.",
                "_code": "INVALID_ARGS", "_suggestion": "Pass an available raster band."}
    provider = layer.dataProvider()
    try:
        from qgis.core import QgsRasterBandStats, QgsRectangle
        stats = provider.bandStatistics(band, enum_member(QgsRasterBandStats, "Stats", "All"), QgsRectangle(), 250000)
    except Exception:  # nosec B110 - provider detail is optional
        try:
            stats = provider.bandStatistics(band)
        except Exception as e:
            return {"_error": f"Band statistics failed: {e}"}
    out = {
        "layer": layer.name(),
        "band": band,
        "band_count": layer.bandCount(),
        "min": stats.minimumValue,
        "max": stats.maximumValue,
        "mean": stats.mean,
        "stddev": stats.stdDev,
        "range": stats.range,
        "sum": stats.sum,
    }
    counted = int(getattr(stats, "elementCount", 0) or 0)
    out["pixels_counted"] = counted







    read = int(getattr(stats, "width", 0) or 0) * int(getattr(stats, "height", 0) or 0)
    if read <= 0:
        read = int(layer.width() or 0) * int(layer.height() or 0)
    coverage = round(100.0 * counted / read, 1) if read > 0 and counted <= read else None
    if coverage is not None:
        out["coverage_pct"] = coverage


    if counted == 0 or not math.isfinite(float(stats.mean)):
        out["note"] = ("No valid pixel: every cell of this band is nodata. The raster was written empty "
                       "(wrong extent or CRS at creation); do not reproject it, recreate it.")
    elif coverage is not None and coverage < COVERAGE_FULL_PCT:
        out["note"] = (f"{coverage}% of the cells carry a value; the rest is nodata, from a clip, a cloud "
                       "mask, or the edge of the source. Every number above describes those cells only. "
                       "Two rasters of the same area whose coverage differs were not measured on the same "
                       "pixels: say so rather than reading the difference as change on the ground.")
    if args.get("class_counts"):
        out.update(_class_counts(layer, provider, band))
    return out





_CLASS_COUNT_PIXELS = 4_000_000
_CLASS_COUNT_MAX_CLASSES = 256


def _class_counts(layer, provider, band: int) -> dict:
    """Share and area per distinct value of a categorical band."""






    try:
        import numpy as np
        from osgeo import gdal
    except Exception as exc:  # nosec B110 - the table is optional
        return {"class_counts_note": f"GDAL or numpy unavailable: {exc}"}
    source = provider.dataSourceUri()
    dataset = gdal.Open(source) if source else None
    if dataset is None:
        return {"class_counts_note": "the source is not a GDAL raster (a tile service or a WMS has no class table)"}
    try:
        width, height = dataset.RasterXSize, dataset.RasterYSize
        scale = max(1.0, math.sqrt(width * height / float(_CLASS_COUNT_PIXELS)))
        buf_w, buf_h = max(1, int(width / scale)), max(1, int(height / scale))
        raster_band = dataset.GetRasterBand(band)
        array = raster_band.ReadAsArray(0, 0, width, height, buf_xsize=buf_w, buf_ysize=buf_h)
        nodata = raster_band.GetNoDataValue()
    except Exception as exc:  # nosec B110 - a read failure is reported, not raised
        return {"class_counts_note": f"could not read the band: {exc}"}
    finally:
        dataset = None
    if array is None:
        return {"class_counts_note": "could not read the band"}
    flat = array.ravel()
    valid = np.ones(flat.shape, dtype=bool)
    if np.issubdtype(flat.dtype, np.floating):
        valid &= np.isfinite(flat)
    if nodata is not None:
        valid &= flat != nodata
    values, counts = np.unique(flat[valid], return_counts=True)
    sampled = int(valid.size)
    out = {"pixels_sampled": sampled,
           "nodata_share_pct": round(100.0 * (1 - valid.sum() / sampled), 2) if sampled else None}
    if scale > 1.0:
        out["class_counts_note"] = (f"read decimated by {scale:.1f} in each direction; shares are exact to a "
                                    "fraction of a percent")
    if len(values) > _CLASS_COUNT_MAX_CLASSES:
        out["class_counts_note"] = (f"continuous band: {len(values)} distinct values in the sample, no class table; "
                                    "threshold it first (raster_calculator) and count the classes of the result")
        return out
    total = int(counts.sum()) or 1
    factor = (width * height) / float(buf_w * buf_h)
    pixel_area = ground.pixel_facts(layer).get("pixel_area_m2")
    classes = []
    for value, count in zip(values.tolist(), counts.tolist()):
        row = {"value": value, "share_pct": round(100.0 * count / total, 2), "pixels": int(round(count * factor))}
        if pixel_area:
            row["area_km2"] = round(count * factor * pixel_area / 1e6, 3)
        classes.append(row)
    classes.sort(key=lambda row: -row["share_pct"])
    out["classes"] = classes
    out["classes_order"] = "largest share first; share_pct is of the valid pixels"
    return out


def _get_provider_capabilities(args: dict) -> dict:
    from qgis.core import QgsVectorDataProvider

    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a vector layer"}

    provider = layer.dataProvider()
    caps = provider.capabilities()
    names = []
    for cap in (
        "AddFeatures", "DeleteFeatures", "ChangeAttributeValues", "ChangeGeometries",
        "AddAttributes", "DeleteAttributes", "RenameAttributes", "CreateSpatialIndex",
        "SelectAtId", "TransactionSupport",
    ):
        flag = getattr(QgsVectorDataProvider, cap, None)
        try:
            if flag is not None and (caps & flag):
                names.append(cap)
        except Exception:  # nosec B112 - geometry fetch hint is optional
            continue
    storage = None
    try:
        storage = provider.storageType()
    except Exception:  # nosec B110 - provider detail is optional
        pass
    return {
        "layer": layer.name(),
        "editable": layer.isEditable(),
        "read_only": layer.readOnly(),
        "storage": storage,
        "capabilities": names,
    }


def _check_geometry_validity(args: dict) -> dict:
    name = args.get("layer_name")
    if not name:


        from qgis.utils import iface
        active = iface.activeLayer() if iface else None
        if not isinstance(active, QgsVectorLayer):
            return {"_error": "No layer named and no vector layer is active.",
                    "_code": "INVALID_ARGS",
                    "suggestion": "Pass layer_name; list_layers shows the names."}
        layer, name = active, active.name()
    else:
        layer = _find_layer(name)
    if not layer:
        return _layer_not_found_error(name)
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{name}' is not a vector layer"}

    limit = min(max(int(args.get("limit", 50) or 50), 1), 500)
    max_checked = 100_000






    budget = VertexBudget()
    max_vertices = budget.per_geometry
    total = layer.featureCount()
    request = QgsFeatureRequest()
    try:
        request.setSubsetOfAttributes([])
    except Exception:  # nosec B110 - geometry fetch hint is optional
        pass

    checked = 0
    invalid = []
    too_large = []
    stopped = None
    complete = False
    for feat in layer.getFeatures(request):
        if checked >= max_checked:
            break
        stopped = budget.exhausted()
        if stopped:
            break
        checked += 1
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        vertices = budget.oversize(geom)
        if vertices:

            too_large.append({"_fid": feat.id(), "vertices": vertices})
            continue
        try:
            if geom.isGeosValid():
                continue
            reasons = _invalid_reasons(geom)
        except Exception:
            reasons = ["invalid geometry"]
        invalid.append({"_fid": feat.id(), "reasons": reasons or ["invalid geometry"]})
        if len(invalid) >= limit:
            break
    else:
        complete = True


    complete = complete or (total >= 0 and checked >= total)
    out = {
        "layer": layer.name(),
        "checked": checked,
        "total": total,
        "invalid_count": len(invalid),
        "invalid": invalid,
        "all_valid": len(invalid) == 0 and complete and not too_large,
        "complete": complete,
        "features_omitted": max(0, total - checked) if total >= 0 else None,
    }
    if too_large:
        out["unchecked_large"] = too_large[:limit]
        out["unchecked_large_count"] = len(too_large)
        out["note"] = (
            f"{len(too_large)} geometr{'y' if len(too_large) == 1 else 'ies'} over {max_vertices:,} "
            f"vertices were counted but not checked: validating one of that size holds QGIS for minutes. "
            f"Run the Processing algorithm native:checkvalidity (run_processing, async) to check them "
            f"in the background, or fix them with native:fixgeometries."
        )
    if stopped:
        out["stopped"] = stopped
        out["suggestion"] = (
            f"The check stopped after {checked} of {total} features to keep QGIS responsive "
            f"({budget.stop_reason(stopped)} reached). For the whole layer run "
            f"native:checkvalidity through run_processing with async true."
        )
    return out


def _invalid_reasons(geom) -> list:
    """Up to three reasons a geometry is invalid, from GEOS."""






    engine = enum_member(QgsGeometry, "ValidationMethod", "ValidatorGeos", None)
    if engine is None:
        try:
            from qgis.core import Qgis
            engine = enum_member(Qgis, "GeometryValidationEngine", "Geos", None)
        except ImportError:
            engine = None
    reasons = []
    try:
        errors = geom.validateGeometry(engine) if engine is not None else geom.validateGeometry()
    except Exception:  # noqa: BLE001 - the verdict stands without its reasons
        return []
    for err in errors:
        reasons.append(err.what())
        if len(reasons) >= 3:
            break
    return reasons
