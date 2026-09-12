# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Processing-backed analysis tools harvested from the other QGIS agent projects."""

















from __future__ import annotations

import os
import re

from qgis.core import (
    QgsApplication,
    QgsMapLayer,
    QgsProcessingFeedback,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import is_raster
from .data_tools import _avoid_reserved_name
from .harvest_analysis import _expand, _field_error, _raster, _units, _vector

ZONAL_STATS = {
    0: "count", 1: "sum", 2: "mean", 3: "median", 4: "stdev", 5: "min",
    6: "max", 7: "range", 8: "minority", 9: "majority", 10: "variety", 11: "variance",
}
JOIN_PREDICATES = {0: "intersects", 1: "contains", 2: "equals", 3: "touches", 4: "overlaps", 5: "within", 6: "crosses"}
JOIN_METHODS = {0: "one-to-many", 1: "first match", 2: "largest overlap"}
_RASTER_CALC_ERRORS = {
    1: "could not create the output file",
    2: "an input layer is invalid",
    3: "the expression does not parse",
    4: "out of memory",
    5: "cancelled",
    6: "a band number is out of range",
    7: "the calculation failed",
}


def register_harvest_processing_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="zonal_statistics",
        input_schema={
            "type": "object",
            "properties": {
                "polygon_layer": {"type": "string"},
                "raster_layer": {"type": "string"},
                "band": {"type": "integer", "minimum": 1},
                "prefix": {"type": "string"},
                "stats": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "maxItems": 20,
                },
                "output_path": {
                    "type": "string",
                },
                "name": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["polygon_layer", "raster_layer"],
        },
        handler=_zonal_statistics,
    ))

    registry.register(Tool(
        name="spatial_join",
        input_schema={
            "type": "object",
            "properties": {
                "target_layer": {"type": "string"},
                "join_layer": {"type": "string"},
                "predicates": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "maxItems": 20,
                },
                "join_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 1000,
                },
                "method": {
                    "oneOf": [
                        {"type": "integer"},
                        {"type": "string", "enum": ["one_to_many", "first_match", "largest_overlap"]},
                    ],
                },
                "prefix": {"type": "string"},
                "output_path": {"type": "string"},
                "name": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["target_layer", "join_layer"],
        },
        handler=_spatial_join,
    ))

    registry.register(Tool(
        name="raster_calculator",
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "maxLength": 80},
                "expression": {
                    "type": "string",
                },
                "output_path": {"type": "string"},
                "reference_layer": {
                    "type": "string",
                },
                "name": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["expression", "output_path"],
        },
        handler=_raster_calculator,
    ))

    registry.register(Tool(
        name="find_processing_algorithm",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "provider": {
                    "type": "string",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000},




                "refresh": {"type": "boolean"},
            },
            "required": ["query"],
        },
        handler=_find_processing_algorithm,
    ))





def _run_alg(alg_id: str, params: dict):
    import processing

    if QgsApplication.processingRegistry().algorithmById(alg_id) is None:
        return None, tool_error(
            f"Algorithm not found: {alg_id}",
            "INVALID_ARGS",
            "Call find_processing_algorithm with a description of the task to get the right id.",
        )
    feedback = QgsProcessingFeedback()
    try:
        result = processing.run(alg_id, params, feedback=feedback)
    except Exception as e:
        return None, tool_error(
            f"{alg_id} failed: {e}",
            "PROCESSING_FAILED",
            "Check the layer CRS with get_layer_crs and the parameters with get_algorithm_help.",
        )
    return result, None


def _run_or_defer(alg_id: str, params: dict, output_name: str):
    """``(result, error, deferred)``: the run, or the task envelope when the inputs are heavy."""












    from .processing_tools import _heavy_inputs, _start_async_processing

    if _heavy_inputs(params):
        alg = QgsApplication.processingRegistry().algorithmById(alg_id)
        if alg is not None:
            return None, None, _start_async_processing(alg, alg_id, params, output_name)
    result, error = _run_alg(alg_id, params)
    return result, error, None


def _register_output(out, name: str) -> dict:
    """Add a processing output to the project and describe it."""
    layer = None
    if isinstance(out, QgsMapLayer):
        layer = out
        layer.setName(name)
    elif isinstance(out, str) and os.path.exists(out):
        layer = QgsVectorLayer(out, name, "ogr")
        if not layer.isValid():
            layer = QgsRasterLayer(out, name)
    if layer is None or not layer.isValid():
        return {"output": out if isinstance(out, str) else str(out), "added_to_project": False}
    QgsProject.instance().addMapLayer(layer)
    info = {
        "layer_id": layer.id(),
        "name": layer.name(),
        "crs": layer.crs().authid(),
        "units": _units(layer.crs()),
        "added_to_project": True,
    }
    if isinstance(layer, QgsVectorLayer):
        info["feature_count"] = layer.featureCount()
        info["fields"] = [f.name() for f in layer.fields()]
    if isinstance(out, str):
        info["path"] = out
    return info


def _output_target(output_path: str | None, memory_name: str) -> str:
    if not output_path:
        return f"memory:{memory_name}"
    path = _expand(output_path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    return path





def _zonal_statistics(args: dict) -> dict:
    poly, error = _vector(args["polygon_layer"])
    if error:
        return error
    rast, error = _raster(args["raster_layer"])
    if error:
        return error
    band = int(args.get("band") or 1)
    if not 1 <= band <= rast.bandCount():
        return tool_error(
            f"band={band} is out of range: {rast.name()!r} has {rast.bandCount()} band(s).",
            "INVALID_ARGS",
            f"Pass a band between 1 and {rast.bandCount()}.",
        )
    stats = [int(s) for s in (args.get("stats") or [0, 1, 2])]
    bad = [s for s in stats if s not in ZONAL_STATS]
    if bad:
        return tool_error(
            f"Unknown stat code(s): {bad}.",
            "INVALID_ARGS",
            "Use codes 0-11: " + ", ".join(f"{k}={v}" for k, v in ZONAL_STATS.items()) + ".",
        )
    prefix = args.get("prefix", "_")
    params = {
        "INPUT": poly,
        "INPUT_RASTER": rast,
        "RASTER_BAND": band,
        "COLUMN_PREFIX": prefix,
        "STATISTICS": stats,
        "OUTPUT": _output_target(args.get("output_path"), "zonal_stats"),
    }
    name = args.get("name") or "zonal_stats"
    result, error, deferred = _run_or_defer("native:zonalstatisticsfb", params, name)
    if deferred is not None:
        deferred["new_columns"] = [f"{prefix}{ZONAL_STATS[s]}" for s in stats]
        deferred["raster_band"] = band
        deferred["value_units"] = (f"raster band {band} values; the count statistic is the number of valid "
                                   f"raster cells inside each zone, not a number of features")
        return deferred
    if error:
        return error
    out = _register_output(result.get("OUTPUT"), name)
    out["stats"] = [ZONAL_STATS[s] for s in stats]
    out["new_columns"] = [f"{prefix}{ZONAL_STATS[s]}" for s in stats]
    out["raster_band"] = band
    out["value_units"] = (f"raster band {band} values of {rast.name()!r}; the count statistic is the number "
                          f"of valid raster cells inside each zone, not a number of features")
    out["zone_count"] = out.get("feature_count")
    return out


def _discard_partial_raster(path: str) -> bool:
    """Remove an output the calculator only half wrote."""





    gone = False
    for candidate in (path, f"{path}.aux.xml"):
        try:
            if os.path.isfile(candidate):
                os.remove(candidate)
                gone = gone or candidate == path
        except OSError:  # nosec B110 - the calculation error is what the caller needs
            continue
    return gone


def _spatial_join(args: dict) -> dict:
    target, error = _vector(args["target_layer"])
    if error:
        return error
    join, error = _vector(args["join_layer"])
    if error:
        return error
    predicates = [int(p) for p in (args.get("predicates") or [0])]
    bad = [p for p in predicates if p not in JOIN_PREDICATES]
    if bad:
        return tool_error(
            f"Unknown predicate code(s): {bad}.",
            "INVALID_ARGS",
            "Use codes 0-6: " + ", ".join(f"{k}={v}" for k, v in JOIN_PREDICATES.items()) + ".",
        )
    raw_method = args.get("method", 1)
    names = {"one_to_many": 0, "first_match": 1, "largest_overlap": 2}





    try:
        method = int(names.get(raw_method, raw_method))
    except (TypeError, ValueError):
        method = None
    if method not in JOIN_METHODS:
        return tool_error(
            f"method {raw_method!r} is not one of {sorted(names)} or {sorted(JOIN_METHODS)}.",
            "INVALID_ARGS",
            "method is 'one_to_many' (0), 'first_match' (1) or 'largest_overlap' (2).")
    join_fields = args.get("join_fields") or []
    missing = [f for f in join_fields if join.fields().indexOf(f) < 0]
    if missing:
        return _field_error(join, missing[0])
    note = None
    if target.crs() != join.crs():
        note = (
            f"target CRS {target.crs().authid()} and join CRS {join.crs().authid()} differ; "
            "QGIS reprojects on the fly for the predicate test"
        )
    params = {
        "INPUT": target,
        "JOIN": join,
        "PREDICATE": predicates,
        "JOIN_FIELDS": join_fields,
        "METHOD": method,
        "PREFIX": args.get("prefix", ""),
        "OUTPUT": _output_target(args.get("output_path"), "joined"),
    }
    name = args.get("name") or "joined"
    result, error, deferred = _run_or_defer("native:joinattributesbylocation", params, name)
    if deferred is not None:
        deferred["predicates"] = [JOIN_PREDICATES[p] for p in predicates]
        deferred["method"] = JOIN_METHODS[method]
        if note:
            deferred["crs_note"] = note
        return deferred
    if error:
        return error
    out = _register_output(result.get("OUTPUT"), name)
    out["predicates"] = [JOIN_PREDICATES[p] for p in predicates]
    out["method"] = JOIN_METHODS[method]
    if "JOINED_COUNT" in result:
        out["joined_count"] = result["JOINED_COUNT"]
        out["count_units"] = "features"
    if note:
        out["crs_note"] = note
    return out





_REF_RE = re.compile(r'"([^"]+)@(\d+)"|([A-Za-z0-9_.\-]+)@(\d+)')




_CALC_MAX_PIXELS = 60_000_000


def _raster_calculator(args: dict) -> dict:
    from qgis.analysis import QgsRasterCalculator, QgsRasterCalculatorEntry

    expression = str(args.get("expression") or "").strip()
    if not expression:
        return tool_error("expression is empty.", "INVALID_ARGS", 'Pass band math such as "dem@1" * 2.')
    wanted_path = str(args.get("output_path") or "").strip()
    temp_words = ("TEMPORARY_OUTPUT", "TEMP", "MEMORY")
    if not wanted_path or wanted_path.upper() in temp_words or wanted_path.startswith("memory:"):



        import tempfile



        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(args.get("name") or "calc")).strip("_") or "calc"
        stem = _avoid_reserved_name(stem)
        wanted_path = os.path.join(tempfile.mkdtemp(prefix="calc-"), f"{stem}.tif")
    output_path = _expand(wanted_path)
    project = QgsProject.instance()

    entries = []
    refs = {}
    ambiguous: dict = {}
    rasters = []
    for layer in project.mapLayers().values():
        if not is_raster(layer):
            continue
        rasters.append(layer)
        for band in range(1, layer.bandCount() + 1):
            entry = QgsRasterCalculatorEntry()
            entry.ref = f"{layer.name()}@{band}"
            entry.raster = layer
            entry.bandNumber = band
            entries.append(entry)



            if entry.ref in refs and refs[entry.ref] is not layer:
                ambiguous.setdefault(entry.ref, [refs[entry.ref].id()]).append(layer.id())
            refs[entry.ref] = layer
    if ambiguous:
        names = sorted({ref.rsplit("@", 1)[0] for ref in ambiguous})
        return tool_error(
            f"More than one loaded raster is named {', '.join(repr(n) for n in names)}, so a band "
            f"reference cannot say which one to read.",
            "INVALID_ARGS",
            "Rename one of them, or remove the one you are not calculating on, then run again. "
            f"The layer ids involved are {sorted({i for ids in ambiguous.values() for i in ids})}.",
        )
    if not rasters:
        return tool_error(
            "No raster layers loaded to compute from.", "INVALID_ARGS", "Load a raster with add_data first."
        )

    referenced = []
    for quoted_name, quoted_band, bare_name, bare_band in _REF_RE.findall(expression):
        referenced.append(f"{quoted_name}@{quoted_band}" if quoted_name else f"{bare_name}@{bare_band}")
    unknown = [r for r in referenced if r not in refs]
    if unknown:
        return tool_error(
            f"Unknown raster reference(s): {unknown}.",
            "INVALID_ARGS",
            f'Available references: {sorted(refs)[:20]}. Double-quote a name with spaces: "S2 B04 Red@1".',
        )

    if args.get("reference_layer"):
        ref_layer, error = _raster(args["reference_layer"])
        if error:
            return error
    elif referenced:
        ref_layer = refs[referenced[0]]
    else:
        ref_layer = rasters[0]

    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    extent = ref_layer.extent()
    cols, rows = ref_layer.width(), ref_layer.height()




    from .processing_tools import _ASYNC_PIXELS, _start_async_processing

    if _ASYNC_PIXELS < cols * rows <= _CALC_MAX_PIXELS:
        alg = QgsApplication.processingRegistry().algorithmById("native:rastercalc")
        if alg is not None:
            crs_id = ref_layer.crs().authid()
            params = {
                "LAYERS": [layer.id() for layer in rasters],
                "EXPRESSION": expression,
                "EXTENT": f"{extent.xMinimum()},{extent.xMaximum()},{extent.yMinimum()},{extent.yMaximum()}"
                          + (f" [{crs_id}]" if crs_id else ""),
                "CELL_SIZE": extent.width() / cols if cols else 0,
                "CRS": crs_id,
                "OUTPUT": output_path,
            }
            started = _start_async_processing(alg, "native:rastercalc", params, args.get("layer_name"))
            started["note"] = (f"{ref_layer.name()} is {cols * rows / 1e6:,.0f} million pixels, so the calculation "
                               "runs in the background as native:rastercalc; poll get_task_status.")
            return started




    if cols * rows > _CALC_MAX_PIXELS:
        return tool_error(
            f"{ref_layer.name()} is {cols:,} by {rows:,} pixels ({cols * rows / 1e6:,.0f} million); the calculator "
            f"runs over the whole grid on the main thread and the cap is {_CALC_MAX_PIXELS / 1e6:,.0f} million.",
            "INVALID_ARGS",
            "Clip every input to the study area first (run_processing gdal:cliprasterbyextent with PROJWIN set to "
            "the canvas extent, or native:cliprasterbymasklayer with the parcels), then calculate on the clips.")



    output_existed = os.path.isfile(output_path)





    crs = ref_layer.crs()
    try:
        calc = QgsRasterCalculator(
            expression, output_path, "GTiff", extent, crs, cols, rows, entries, project.transformContext()
        )
    except TypeError:
        try:
            calc = QgsRasterCalculator(
                expression, output_path, "GTiff", extent, cols, rows, entries, project.transformContext()
            )
        except TypeError:
            calc = QgsRasterCalculator(expression, output_path, "GTiff", extent, cols, rows, entries)
    code = int(calc.processCalculation())
    if code != 0:
        reason = _RASTER_CALC_ERRORS.get(code, f"error code {code}")
        suggestion = "Check the expression and the band numbers."
        if code == 3:
            suggestion = (
                'Reference bands as "Layer name@1" with double quotes; operators are + - * / ^ and comparisons.'
            )
        elif code == 1:
            suggestion = "Pass an output_path in a writable folder with a .tif extension."
        elif code == 4:





            suggestion = (
                "Cut the area, not the expression: the calculator holds every input band of the whole "
                "extent in memory at once. Clip the inputs with gdal:cliprasterbymasklayer, or pass a "
                "smaller extent, and calculate on the clips."
            )



        removed = False if output_existed else _discard_partial_raster(output_path)
        if removed:
            suggestion += " The partly written output was removed."
        elif output_existed:
            suggestion += (f" {os.path.basename(output_path)} was already there before this run and was left "
                           f"alone; it may now hold the previous result or a partial write.")
        return tool_error(f"Raster calculation failed: {reason}.", "RASTER_CALC_FAILED", suggestion)

    name = args.get("name") or os.path.splitext(os.path.basename(output_path))[0]
    layer = QgsRasterLayer(output_path, name)
    if not layer.isValid():
        return {"path": output_path, "reference_layer": ref_layer.name(), "added_to_project": False}
    project.addMapLayer(layer)
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "path": output_path,
        "reference_layer": ref_layer.name(),
        "band_count": layer.bandCount(),
        "width": layer.width(),
        "height": layer.height(),
        "size_units": "pixels",
        "crs": layer.crs().authid(),
        "units": _units(layer.crs()),
        "extent": {
            "xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(), "ymax": extent.yMaximum(),
        },
        "added_to_project": True,
    }




_ALGORITHM_CATALOG: list[dict] | None = None

_STOPWORDS = {
    "the", "a", "an", "of", "to", "for", "with", "and", "or", "in", "on", "by",
    "from", "layer", "layers", "using", "each", "all", "my", "this", "that",
    "create", "make", "new", "map", "file", "data",
}



_SYNONYMS = {
    "merge": ["merge", "union", "dissolve"],
    "combine": ["union", "merge", "dissolve"],
    "join": ["join", "union"],
    "clip": ["clip", "mask", "extract"],
    "crop": ["clip", "mask"],
    "cut": ["clip"],
    "average": ["mean", "statistics", "zonal"],
    "statistics": ["statistics", "stats", "zonal"],
    "stats": ["statistics", "zonal"],
    "reproject": ["reproject", "warp", "crs", "transform"],
    "projection": ["reproject", "crs"],
    "distance": ["distance", "buffer", "proximity"],
    "simplify": ["simplify", "generalize", "smooth"],
    "interpolate": ["interpolate", "idw", "tin"],
    "slope": ["slope", "terrain"],
    "elevation": ["dem", "terrain", "elevation"],
    "centroid": ["centroid", "center"],
    "intersect": ["intersection", "clip", "overlay"],
    "erase": ["difference", "erase"],
    "nearest": ["nearest", "neighbour", "neighbor", "hub", "distance"],
    "count": ["count", "points", "polygon"],
    "grid": ["grid", "fishnet", "tessellation"],
    "raster": ["raster", "rasterize", "gdal"],
    "vector": ["vector", "polygonize", "vectorize"],
    "hillshade": ["hillshade", "shaded", "relief"],
    "viewshed": ["viewshed", "visibility"],
    "watershed": ["watershed", "basin", "catchment", "hydrology"],
    "dissolve": ["dissolve", "aggregate"],
    "split": ["split", "explode"],
    "convert": ["convert", "translate", "export"],
    "smooth": ["smooth", "simplify"],
    "fill": ["fill", "sink", "nodata"],
    "classify": ["reclassify", "classify"],
    "reclass": ["reclassify"],
    "area": ["area", "geometry", "attributes"],
    "field": ["field", "attribute", "calculator"],
}


def _algorithm_catalog(refresh: bool = False) -> list[dict]:
    """Every registered algorithm with searchable metadata, cached once per session."""
    global _ALGORITHM_CATALOG
    if _ALGORITHM_CATALOG is not None and not refresh:
        return _ALGORITHM_CATALOG
    catalog = []
    for alg in QgsApplication.processingRegistry().algorithms():
        try:
            tags = [str(t).lower() for t in (alg.tags() or [])]
        except Exception:
            tags = []
        try:
            description = alg.shortDescription() or ""
        except Exception:
            description = ""
        try:
            provider = alg.provider().id()
        except Exception:
            provider = ""
        catalog.append({
            "id": alg.id(),
            "name": alg.displayName(),
            "provider": provider,
            "tags": tags,
            "description": description,
        })
    _ALGORITHM_CATALOG = catalog
    return catalog


def _query_tokens(query: str) -> list[str]:
    """Tokenize a task description and expand with GIS synonyms."""
    words = re.findall(r"[a-z]+", (query or "").lower())
    tokens = [w for w in words if w not in _STOPWORDS and len(w) > 2]
    expanded = list(tokens)
    for token in tokens:
        expanded.extend(_SYNONYMS.get(token, []))
    return list(dict.fromkeys(expanded))


def _score_algorithm(tokens: list[str], entry: dict) -> float:
    """Cheap lexical relevance of one catalog entry against query tokens."""
    name_words = set(re.findall(r"[a-z]+", entry["name"].lower()))
    alg_id = entry["id"].lower()
    tags = entry["tags"]
    desc = entry["description"].lower()
    score = 0.0
    for token in tokens:
        if token in name_words:
            score += 3.0
        elif any(token in w for w in name_words):
            score += 1.5
        if token in alg_id:
            score += 1.5
        if any(token == tag or token in tag for tag in tags):
            score += 2.0
        if desc and token in desc:
            score += 0.5

    if score > 0 and entry["provider"] == "native":
        score += 0.5
    return score


def _find_processing_algorithm(args: dict) -> dict:
    query = str(args.get("query") or "")
    catalog = _algorithm_catalog(refresh=bool(args.get("refresh")))
    provider = (args.get("provider") or "").strip().lower()
    if provider:
        catalog = [e for e in catalog if e["provider"].lower() == provider]
    tokens = _query_tokens(query)
    for keyword in args.get("keywords") or []:
        for token in _query_tokens(str(keyword)):
            if token not in tokens:
                tokens.append(token)
    if not tokens:
        return tool_error(
            "The query has no searchable words.",
            "INVALID_ARGS",
            "Describe the task, for example 'buffer polygons by 50 m'.",
        )
    limit = max(1, min(int(args.get("limit") or 15), 50))
    scored = [(_score_algorithm(tokens, e), e) for e in catalog]
    scored = [(s, e) for s, e in scored if s > 0]
    scored.sort(key=lambda item: item[0], reverse=True)
    matches = [dict(e, score=round(s, 1)) for s, e in scored[:limit]]
    out = {
        "query": query,
        "tokens": tokens,
        "matches": matches,
        "count": len(matches),
        "total": len(_algorithm_catalog()),
        "next": "Call get_algorithm_help with the chosen id, then run_processing.",
    }
    if not matches:
        out["suggestion"] = "Nothing scored. Try other words, add keywords, or call list_algorithms."
    return out
