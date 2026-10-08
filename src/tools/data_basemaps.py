# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import json
import os
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

from ..core import catalog, limits, net, security
from ..core.background import on_main_thread, run_on_main_thread, take
from ..core.context import quickmapservices_started
from ..core.follow import view_kept
from ..core.layer_order import COVERAGE_PROPERTY, cover_report, feature_count_of, place_basemap
from ..core.logger import log_warning
from ..core.provider_uri import encode_uri_url
from ..core.tool_registry import ServedValueMissing, coded_fact, tool_error
from .data_common import _canvas_viewbox_4326, _http_get, _viewbox_bounds, built_here, worker_options








def _qms_catalog():

    if not quickmapservices_started():
        return None
    try:
        from quick_map_services.data_sources_list import DataSourcesList
    except Exception:
        return None
    try:
        return DataSourcesList().data_sources
    except Exception:
        return None


def _browser_xyz_connections() -> dict:

    from qgis.core import QgsSettings
    out = {}
    settings = QgsSettings()
    settings.beginGroup("qgis/connections-xyz")
    for name in settings.childGroups():
        url = settings.value(f"{name}/url")
        if url:
            out[name] = {
                "url": url,
                "zmax": settings.value(f"{name}/zmax", 19),
                "zmin": settings.value(f"{name}/zmin", 0),
            }
    settings.endGroup()
    return out


def _list_xyz_sources(args: dict) -> dict:
    qms_catalog = _qms_catalog()
    if qms_catalog is not None:
        by_group: dict = {}
        for ds_id, ds in qms_catalog.items():
            group = getattr(ds, "group", None) or "other"
            by_group.setdefault(group, []).append({
                "id": ds_id,
                "name": getattr(ds, "alias", ds_id),
                "type": getattr(ds, "type", None),
            })
        for items in by_group.values():
            items.sort(key=lambda s: (s["name"] or ""))
        result = {
            "provider": "QuickMapServices",
            "groups": by_group,
            "count": len(qms_catalog),
            **coded_fact(hint="xyz_sources_listed"),
        }
        browser = _browser_xyz_connections()
        if browser:
            result["browser_xyz_connections"] = sorted(browser.keys())
        return result


    browser = _browser_xyz_connections()
    if browser:
        return {
            "provider": "Browser XYZ connections",
            "sources": [{"id": n, "name": n, "url": c["url"]} for n, c in browser.items()],
            "count": len(browser),
            "_warning": "QuickMapServices not importable; showing your Browser XYZ connections.",
        }
    presets = catalog.basemaps()
    return {
        "provider": "built-in presets",
        "sources": [{"id": k, "name": v["name"], "max_zoom": v["max_zoom"], "attribution": v["attribution"]}
                    for k, v in presets.items()],
        "count": len(presets),
        "_warning": "QuickMapServices not importable; using built-in presets.",
    }






_BASEMAP_SYNONYMS = {"openstreetmap": "osm", "mapnik": "standard"}


def _basemap_words(text: str) -> set:

    return {_BASEMAP_SYNONYMS.get(word, word)
            for word in re.split(r"[^0-9a-z]+", (text or "").lower()) if word}


def _resolve_qms_source(catalog: dict, source: str):

    if source in catalog:
        return catalog[source], None
    low = source.strip().lower()
    matches = [ds for ds in catalog.values() if (getattr(ds, "alias", "") or "").strip().lower() == low]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        labels = [f"{getattr(d, 'id', '?')} ({getattr(d, 'group', '?')})" for d in matches]
        return None, {"_error": f"Ambiguous basemap '{source}', matches: {', '.join(labels)}.",
                      "_code": "INVALID_ARGS"}



    if _basemap_words(low) <= {"osm", "standard"} and "osm_mapnik" in catalog:
        return catalog["osm_mapnik"], None

    subs = [ds_id for ds_id in catalog if low in ds_id.lower()]
    if len(subs) == 1:
        return catalog[subs[0]], None










    wanted = _basemap_words(source)
    if wanted:
        scored = [(len(_basemap_words(f"{ds_id} {getattr(ds, 'alias', '') or ''}") - wanted)
                   + (1 if str(getattr(ds, "type", "") or "").upper() == "MVT" else 0), ds_id, ds)
                  for ds_id, ds in catalog.items()
                  if wanted <= _basemap_words(f"{ds_id} {getattr(ds, 'alias', '') or ''}")]
        if scored:
            fewest = min(extra for extra, _, _ in scored)
            best = [(ds_id, ds) for extra, ds_id, ds in scored if extra == fewest]
            if len(best) == 1:
                return best[0][1], None
            return None, {"_error": f"Several basemaps match '{source}': "
                          f"{', '.join(ds_id for ds_id, _ in sorted(best)[:6])}.",
                          "_code": "INVALID_ARGS"}
    return None, None


def _twin_of(layer, candidates) -> object | None:











    if layer is None:
        return None
    try:
        uri = layer.source()
    except Exception:  # noqa: BLE001
        return None
    for other in candidates:
        if other is None or other is layer:
            continue



        try:
            same = other.source() == uri
        except Exception:  # noqa: BLE001
            same = False
        if same:
            return other
    return None


def _stacked(layer) -> dict:















    try:
        place_basemap(layer)
        node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if node is not None:
            node.setExpanded(False)
        return cover_report(layer)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Basemap not placed on top of the basemap block: {exc}")
        return {}


def _preset_named(presets: dict, source: str) -> dict | None:





    wanted = "".join(ch for ch in str(source or "").lower() if ch.isalnum())
    if not wanted:
        return None
    for preset in presets.values():
        if not isinstance(preset, dict):
            continue
        name = "".join(ch for ch in str(preset.get("name") or "").lower() if ch.isalnum())
        if name and name == wanted:
            return preset
    return None


def _withdrawn_basemap(*urls) -> dict | None:










    for url in urls:
        host = urllib.parse.urlsplit(str(url or "")).hostname or ""
        reason = net.withdrawn_reason(host)
        if reason:
            return tool_error(reason, "INVALID_ARGS", hint="basemap_withdrawn", host=host)
    return None


def _chained_preset(source) -> dict | None:







    text = str(source or "").strip()
    if not text or _looks_like_xyz_url(text):
        return None
    try:
        presets = catalog.basemaps()
    except Exception:  # noqa: BLE001
        return None
    preset = presets.get(text.lower()) or _preset_named(presets, text)
    if preset is None:
        return None
    return preset if preset.get("kind") == "vectortile" or preset.get("fallback") else None


def add_xyz_runs_in_background(args: dict) -> bool:






    source = str((args or {}).get("source") or "")
    return _chained_preset(source) is not None or _looks_like_xyz_url(source)




_TILE_CHECK_TIMEOUT_S = 8


def _tile_check(url: str, zmin: int, zmax: int, bbox=None) -> dict | None:






    import math

    view = _viewbox_bounds(run_on_main_thread(_canvas_viewbox_4326))
    area = _viewbox_bounds(_coverage_text(bbox)) if bbox else None
    box = view or area
    if box is None:
        return None
    if area and view and not (area[0] <= (view[0] + view[2]) / 2 <= area[2]
                              and area[1] <= (view[1] + view[3]) / 2 <= area[3]):
        box = area
    west, south, east, north = box
    lon, lat = (west + east) / 2, max(min((south + north) / 2, 85.0), -85.0)
    width = max(east - west, 1e-6)
    z = max(zmin, min(zmax, int(math.floor(math.log2(360.0 / width)))))
    n = 2 ** z
    x = min(n - 1, max(0, int((lon + 180.0) / 360.0 * n)))
    rad = math.radians(lat)
    y = min(n - 1, max(0, int((1.0 - math.asinh(math.tan(rad)) / math.pi) / 2.0 * n)))
    tile = (url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{-y}", str(n - 1 - y))
            .replace("{y}", str(y)))
    if "{" in tile:
        return None
    out = {"z": z, "x": x, "y": y}
    try:
        body = _http_get(tile, timeout=_TILE_CHECK_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        return {**out, "status": "failed", "reason": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {**out, "status": "failed", "reason": net.describe_failure(exc) or str(exc)[:120]}
    if not body:
        return {**out, "status": "failed", "reason": "an empty answer"}
    return {**out, "status": "ok", "bytes": len(body)}



_PRESET_HOPS = 3



_PROBE_TIMEOUT_S = 8
_PROBE_CACHE_S = 300.0


def _raster_unreachable(preset: dict) -> str:

    url = str(preset.get("url") or "")
    for key in ("{z}", "{x}", "{y}", "{-y}"):
        url = url.replace(key, "0")
    try:
        body = _http_get(url, timeout=_PROBE_TIMEOUT_S, cache_ttl=_PROBE_CACHE_S)
    except urllib.error.HTTPError as exc:
        return f"its zoom 0 tile answered HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return net.describe_failure(exc) or str(exc)
    return "" if body else "its zoom 0 tile came back empty"


def _add_preset_chain(preset: dict, source) -> dict:









    presets = catalog.basemaps()
    failures: list = []
    seen: set = set()
    current = preset
    while current is not None and current["id"] not in seen and len(seen) < _PRESET_HOPS:
        seen.add(current["id"])
        label = current["name"]
        if current.get("kind") == "vectortile":
            out = _add_vector_tile_layer({"url": current["url"], "style": current.get("style") or "",
                                          "name": label, "attribution": current.get("attribution") or ""})
        else:
            raster = current
            why = _raster_unreachable(raster) if raster.get("fallback") else ""
            out = {"_error": why} if why else run_on_main_thread(
                lambda raster=raster, label=label: _build_raw_xyz(
                    raster["url"], label, raster["max_zoom"], 0, source, attribution=raster.get("attribution", "")),
                timeout=60)
        if not out.get("_error"):
            out["basemap"] = current["id"]
            if failures:
                out["_warning"] = (f"{preset['name']} did not load, so {current['name']} was added instead. "
                                   + " ".join(failures))
            return out
        failures.append(f"{current['name']}: {out.get('_error')}")
        current = presets.get(str(current.get("fallback") or ""))
    return tool_error("No basemap of this preset's chain could be loaded. " + " ".join(failures),
                      "EXECUTION_FAILED", hint="basemap_chain_failed", preset=str(preset.get("name") or ""))


def _add_xyz_layer(args: dict) -> dict:





    source = args["source"]
    name = args.get("name")



    chained = _chained_preset(source)
    if chained is not None:
        return _add_preset_chain(chained, source)



    if _looks_like_xyz_url(source):
        refused = _withdrawn_basemap(source)
        if refused:
            return refused
        zmin, zmax = _raw_zmin(args.get("zmin")), _raw_zmax(source, args.get("zmax"))

        checked = None if on_main_thread() else _tile_check(source, zmin, zmax, args.get("bbox"))
        out = run_on_main_thread(
            lambda: _build_raw_xyz(source, name or urllib.parse.urlsplit(source).hostname or "XYZ Tiles",
                                   zmax, zmin, source, bbox=args.get("bbox")),
            timeout=60)
        if checked and isinstance(out, dict) and not out.get("_error"):
            out["tile_check"] = checked
            if checked["status"] != "ok":
                out["warning"] = (f"The layer is in the project, but the tile of this view at zoom "
                                  f"{checked['z']} did not come back ({checked['reason']}): the map may "
                                  "show nothing here.")
                out.update(coded_fact(hint="tile_not_returned", z=checked["z"], reason=str(checked["reason"])))
        return out


    qms_catalog = _qms_catalog()
    if qms_catalog is not None and not _looks_like_xyz_url(source):
        ds, err = _resolve_qms_source(qms_catalog, source)
        if err:
            return err
        if ds is not None:
            refused = _withdrawn_basemap(getattr(ds, "tms_url", ""), getattr(ds, "wms_url", ""))
            if refused:
                return refused
            try:
                from quick_map_services.qgis_map_helpers import add_layer_to_map
            except Exception as exc:
                return {"_error": f"QuickMapServices present but add_layer_to_map unavailable: {exc}"}
            project = QgsProject.instance()
            before = dict(project.mapLayers())
            try:


                with view_kept():
                    add_layer_to_map(ds)
            except Exception as exc:
                return {"_error": f"QMS failed to add '{getattr(ds, 'alias', source)}': {exc}",
                        "_code": "QMS_ADD_FAILED"}
            added = [layer for lid, layer in project.mapLayers().items()
                     if lid not in before and layer is not None]





            twins = [_twin_of(layer, before.values()) for layer in added]
            if added and all(twins):
                from .processing_run import remove_layers

                kept = twins[0]
                remove_layers(added)
                return {
                    "provider": "QuickMapServices",
                    "source_id": getattr(ds, "id", source),
                    "layer_id": kept.id(),
                    "layer_name": kept.name(),
                    "type": getattr(ds, "type", None),
                    "already_present": True,
                    "message": f"'{kept.name()}' already reads this exact source, so it was reused.",
                    **coded_fact(hint="basemap_reused", layer=kept.name()),
                }

            out = {
                "provider": "QuickMapServices",
                "source_id": getattr(ds, "id", source),
                "layer_id": added[0].id() if added else None,
                "layer_name": added[0].name() if added else getattr(ds, "alias", source),
                "type": getattr(ds, "type", None),
            }
            if added:
                out.update(_stacked(added[0]))
            return out


    browser = _browser_xyz_connections()
    if source in browser:
        conn = browser[source]
        return (_withdrawn_basemap(conn["url"])
                or _build_raw_xyz(conn["url"], source, conn.get("zmax", 19), conn.get("zmin", 0), source))


    presets = catalog.basemaps()
    builtin = presets.get(str(source).strip().lower()) or _preset_named(presets, source)
    if builtin is not None:
        return _build_raw_xyz(builtin["url"], builtin["name"], builtin["max_zoom"], 0, source,
                              attribution=builtin.get("attribution", ""))



    if not presets and not qms_catalog:
        raise ServedValueMissing("basemaps")


    available = list(presets.keys()) or sorted(qms_catalog.keys())[:25]
    refusal = {
        **tool_error(f"Unknown basemap '{source}'.", "INVALID_ARGS", hint="unknown_basemap", source=str(source)),
        "examples": available,
    }
    if qms_catalog:
        refusal["quickmapservices_count"] = len(qms_catalog)
    return refusal


VECTOR_TILE_EXTENSIONS = (".pbf", ".mvt")

_CACHE_STYLE_S = 3600


def _fetch_gl_style(url: str):

    try:
        raw = _http_get(url, timeout=20, cache_ttl=_CACHE_STYLE_S)
    except (urllib.error.URLError, OSError) as exc:
        return None, f"style not fetched ({exc})"
    try:
        style = json.loads(raw)
    except ValueError as exc:
        return None, f"style is not JSON ({exc})"
    if not isinstance(style, dict) or "layers" not in style:
        return None, "style is not a MapBox GL style document"
    return style, ""


_GEOMETRY_TYPE = ["geometry-type"]


def _geometry_match(node):









    if len(node) != 5 or node[1] != _GEOMETRY_TYPE or not isinstance(node[3], bool) \
            or not isinstance(node[4], bool) or node[3] == node[4]:
        return node
    labels = node[2] if isinstance(node[2], list) else [node[2]]
    if not labels or not all(isinstance(label, str) for label in labels):
        return node
    if node[3]:
        return ["any"] + [["==", _GEOMETRY_TYPE, label] for label in labels]
    return ["all"] + [["!=", _GEOMETRY_TYPE, label] for label in labels]


def _normalised_gl_style(value):

    if isinstance(value, dict):
        return {key: _normalised_gl_style(item) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) == 5 and value[0] == "match":
            value = _geometry_match(value)
        return [_normalised_gl_style(item) for item in value]
    return value


def _apply_gl_style(layer, style: dict) -> tuple[bool, str]:






    try:
        from qgis.core import QgsMapBoxGlStyleConversionContext, QgsMapBoxGlStyleConverter
    except ImportError:
        return False, "this QGIS has no MapBox GL style converter"
    converter = QgsMapBoxGlStyleConverter()
    context = QgsMapBoxGlStyleConversionContext()
    try:
        result = converter.convert(_normalised_gl_style(style), context)
    except Exception as exc:  # noqa: BLE001
        return False, f"style not applied ({exc})"
    if int(result) != 0:
        return False, f"style not applied ({converter.errorMessage() or 'conversion failed'})"
    renderer = converter.renderer()
    if renderer is not None:
        layer.setRenderer(renderer)
    labeling = converter.labeling()
    if labeling is not None:
        layer.setLabeling(labeling)
    return True, ""


def _resolve_tilejson(url: str):











    try:
        raw = _http_get(url, timeout=20, cache_ttl=_CACHE_STYLE_S)
    except (urllib.error.URLError, OSError) as exc:
        return None, f"TileJSON not fetched ({exc})", 0, ""
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return None, f"not a TileJSON document ({exc})", 0, ""
    if not isinstance(doc, dict):
        return None, "not a TileJSON document", 0, ""
    tiles = doc.get("tiles")
    template = ""
    if isinstance(tiles, list):
        for candidate in tiles:
            if isinstance(candidate, str) and _looks_like_xyz_url(candidate):
                template = candidate
                break
    if not template:
        return None, "the TileJSON names no usable tile template", 0, ""
    try:
        zmin = int(doc.get("minzoom", 0))
        zmax = int(doc.get("maxzoom", 14))
    except (TypeError, ValueError):
        zmin, zmax = 0, 14
    return template, zmin, zmax, str(doc.get("attribution") or "")






_OAPIF_ITEMS_RE = re.compile(r"^(?P<collection>.*/collections/[^/]+)/items/?$")


def _oapif_collection(url: str) -> str | None:

    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    found = _OAPIF_ITEMS_RE.match(parts.path)
    if not found:
        return None
    return f"{parts.scheme}://{parts.netloc}{found.group('collection')}"


def _oapif_box(url: str, bbox) -> list | None:


    if bbox:
        return [float(v) for v in bbox[:4]]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    crs = str(query.get("bbox-crs") or "").lower()
    if crs and not crs.endswith(("crs84", "/4326", ":4326")):
        return None
    try:
        values = [float(v) for v in str(query.get("bbox") or "").split(",")]
    except ValueError:
        return None

    box = values[:2] + values[3:5] if len(values) == 6 else values
    return box if len(box) == 4 and box[0] < box[2] and box[1] < box[3] else None


def _oapif_local_copy(collection: str, name: str, box: list | None = None) -> dict | None:












    if on_main_thread():
        return None
    try:
        from osgeo import gdal
    except Exception:  # noqa: BLE001
        return None
    from ..core.host_platform import remove_quietly
    from ..core.policy import create_managed_temp_dir
    from .data_inspect import _safe_extract_stem


    net.check_url(collection)
    source = f"OAPIF:{collection}"



    spat = ["-spat", *(repr(v) for v in box), "-spat_srs", "EPSG:4326"] if box else []


    ceiling = int(limits.current("MAX_FEATURES_MATERIALISED"))
    cancel = net.current_cancel_check()
    path = os.path.join(create_managed_temp_dir("extract"), f"{_safe_extract_stem(name)}.gpkg")
    try:
        written = gdal.VectorTranslate(
            path, source, format="GPKG", options=["-limit", str(ceiling + 1), *spat],
            callback=lambda _done, _message, _data: 0 if (cancel is not None and cancel()) else 1)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"OGC API - Features copy of {collection} failed: {exc}")
        written = None
    if written is None or (cancel is not None and cancel()):
        written = None
        remove_quietly(path)
        return {"cancelled": True} if cancel is not None and cancel() else None
    copied = written.GetLayer(0).GetFeatureCount() if written.GetLayerCount() else 0
    written = None
    if copied > ceiling:
        remove_quietly(path)
        return None
    return {"path": path, "feature_count": int(copied)}


def _add_oapif_layer(args: dict) -> dict:













    url = args.get("url") or ""
    collection = _oapif_collection(url)
    if not collection:
        return {"_error": f"{url} is not an OGC API - Features items endpoint.",
                "code": "INVALID_ARGS",
                "suggestion": "url must have the shape .../collections/<id>/items."}
    name = args.get("name") or collection.rsplit("/", 1)[-1]
    box = _oapif_box(url, args.get("bbox"))
    local = _oapif_local_copy(collection, name, box)
    if local is not None and local.get("cancelled"):
        return {"_error": "The run was stopped while the collection was being read.", "code": "CANCELLED"}
    if local is not None:
        built = built_here(lambda: QgsVectorLayer(local["path"], name, "ogr", worker_options()))

        def _create_local():
            layer = take(built)
            if layer is None:
                layer = QgsVectorLayer(local["path"], name, "ogr")
            if not layer.isValid():
                return {"_invalid": True}
            QgsProject.instance().addMapLayer(layer)
            return {"layer_name": layer.name(), "layer_id": layer.id(),
                    "feature_count": local["feature_count"], "url": collection,
                    "provider": "OGC API - Features, local copy", **({"bbox": box} if box else {})}

        out = run_on_main_thread(_create_local, timeout=60)
        if not out.get("_invalid"):
            return out





    built = built_here(lambda: QgsVectorLayer(f"OAPIF:{collection}", name, "ogr", worker_options()),
                       read_extent=False)

    def _create():
        layer = take(built)
        if layer is None:
            layer = QgsVectorLayer(f"OAPIF:{collection}", name, "ogr")
        if not layer.isValid():
            return {"_invalid": True}
        QgsProject.instance().addMapLayer(layer)


        return {"layer_name": layer.name(), "layer_id": layer.id(),
                "feature_count": feature_count_of(layer), "url": collection,
                "provider": "OGC API - Features",
                **({"warning": "The bbox was not applied: no local copy of the box was made (over "
                               "the feature ceiling, or the copy failed), so the collection is read in "
                               "place, whole."} if box else {})}

    out = run_on_main_thread(_create, timeout=60)
    if out.get("_invalid"):
        return tool_error(f"The OGC API - Features collection at {collection} would not load.",
                          "EXECUTION_FAILED", hint="oapif_collection_not_loaded", collection=str(collection))
    return out


def _add_vector_tile_layer(args: dict) -> dict:







    url = str(args.get("url") or args.get("source") or "").strip()
    tilejson_note = ""
    tilejson_attribution = ""
    resolved_zoom = None
    if url and not _looks_like_xyz_url(url):

        template, a, b, tilejson_attribution = _resolve_tilejson(url)
        if template is None:




            looks_like_style = "style" in urllib.parse.urlparse(url).path.lower()



            return tool_error(f"{url} is neither a tile template nor a TileJSON document ({a}).",
                              "INVALID_ARGS", hint="tile_source_not_template",
                              variant="style" if looks_like_style else "")


        probe = template.replace("{z}", "0").replace("{x}", "0").replace("{y}", "0").replace("{s}", "a")
        template_error = security.validate_url(probe)
        if template_error:
            return {"_error": f"The TileJSON at {url} names a tile template that is not fetched: {template_error}",
                    "_code": "PERMISSION_DENIED"}
        tilejson_note = f"tile template read from the TileJSON at {url}"
        url, resolved_zoom = template, (a, b)
    if not _looks_like_xyz_url(url):
        return {"_error": "A vector tile source is a template with {z}, {x} and {y}.",
                "_code": "INVALID_ARGS",
                "_suggestion": "source is the tile template, e.g. "
                               "https://host/tms/1.0.0/LAYER/{z}/{x}/{y}.pbf"}

    base = posixpath.basename(urllib.parse.urlparse(url).path)
    if "{" in base:
        base = urllib.parse.urlparse(url).hostname or ""
    name = args.get("name") or base or "Vector tiles"
    zmin, zmax = _zoom_range(args)





    if resolved_zoom is not None and args.get("zmin") is None and args.get("zmax") is None:
        zmin, zmax = resolved_zoom
    style_url = str(args.get("style") or "").strip()
    style, style_note = (None, "")
    if style_url:
        style, style_note = _fetch_gl_style(style_url)

    uri = f"type=xyz&url={encode_uri_url(url)}&zmin={zmin}&zmax={zmax}"

    def _create():
        from qgis.core import QgsVectorTileLayer

        layer = QgsVectorTileLayer(uri, name)
        if not layer.isValid():
            return tool_error(f"QGIS could not open the vector tile service at {url}.", "INVALID_ARGS",
                              hint="vector_tiles_unopenable")
        out = {"layer_name": layer.name(), "layer_id": layer.id(), "url": url,
               "provider": "vector tiles", "zmin": zmin, "zmax": zmax}
        if tilejson_note:
            out["resolved_from"] = tilejson_note

        credit = str(args.get("attribution") or "").strip() or tilejson_attribution
        if credit:
            _credit(layer, credit)
            out["attribution"] = credit
        if style is not None:
            applied, why = _apply_gl_style(layer, style)
            out["styled"] = applied
            if not applied:
                out["_note"] = why
        elif style_note:
            out["_note"] = style_note
        elif style_url == "":




            out.update(coded_fact(hint="vector_tiles_no_style"))
        with view_kept():
            QgsProject.instance().addMapLayer(layer)
        out.update(_stacked(layer))
        return out

    return run_on_main_thread(_create, timeout=60)


def _zoom_range(args: dict) -> tuple[int, int]:
    def _clamp(value, fallback):
        try:
            return max(0, min(int(value), 24))
        except (TypeError, ValueError):
            return fallback

    zmin = _clamp(args.get("zmin"), 0)
    zmax = _clamp(args.get("zmax"), 19)
    return (zmin, zmax) if zmin <= zmax else (zmax, zmin)


def _looks_like_xyz_url(source: str) -> bool:
    return "{z}" in source and "{x}" in source and "{y}" in source







_GIBS_LEVEL_RE = re.compile(r"GoogleMapsCompatible_Level(\d{1,2})\b")


def _raw_zmax(url: str, asked=None) -> int:

    try:
        if asked is not None and 0 <= int(asked) <= 24:
            return int(asked)
    except (TypeError, ValueError):
        pass
    found = _GIBS_LEVEL_RE.search(str(url or ""))
    return int(found.group(1)) if found else 19


def _raw_zmin(asked=None) -> int:
    try:
        return int(asked) if asked is not None and 0 <= int(asked) <= 24 else 0
    except (TypeError, ValueError):
        return 0


def _coverage_text(bbox) -> str:

    try:
        west, south, east, north = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return ""
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return ""
    return f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"


def _build_raw_xyz(url: str, name: str, zmax, zmin, source_label: str, attribution: str = "",
                   bbox=None) -> dict:
    uri = f"type=xyz&url={encode_uri_url(url)}&zmax={zmax}&zmin={zmin}"
    layer = QgsRasterLayer(uri, name, "wms")
    if not layer.isValid():
        return {"_error": f"Failed to create XYZ layer from: {source_label}"}
    _credit(layer, attribution)



    covered = _coverage_text(bbox) if bbox else ""
    if covered:
        layer.setCustomProperty(COVERAGE_PROPERTY, covered)




    kept = _twin_of(layer, QgsProject.instance().mapLayers().values())
    if kept is not None:
        return {"layer_name": kept.name(), "layer_id": kept.id(), "source": source_label,
                "already_present": True,
                "message": f"'{kept.name()}' already reads these tiles, so it was reused."}
    with view_kept():
        QgsProject.instance().addMapLayer(layer)
    out = {"layer_name": layer.name(), "layer_id": layer.id(), "source": source_label}
    if attribution:
        out["attribution"] = attribution
    out.update(_stacked(layer))
    return out


def _credit(layer, attribution: str) -> None:








    text = (attribution or "").strip()[:500]
    if not text:
        return
    try:
        from ..core.licence import set_layer_attribution

        set_layer_attribution(layer, text)
        metadata = layer.metadata()
        if not metadata.rights():
            metadata.setRights([text])
            layer.setMetadata(metadata)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Attribution not set on {layer.name()}: {exc}")
