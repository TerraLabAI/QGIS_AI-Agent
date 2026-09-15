# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Basemaps and tile sources: QuickMapServices, XYZ, GL styles, TileJSON, OAPIF and vector tile layers."""

from __future__ import annotations

import json
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

from ..core import catalog, net, security
from ..core.follow import view_kept
from ..core.layer_order import cover_report, place_basemap
from ..core.logger import log_warning
from ..core.provider_uri import encode_uri_url
from .data_common import _http_get, _run_on_main_thread








def _qms_catalog():
    """Return QMS's {id: DataSourceInfo} catalog, or None if QMS is unavailable."""
    try:
        from quick_map_services.data_sources_list import DataSourcesList
    except Exception:
        return None
    try:
        return DataSourcesList().data_sources
    except Exception:
        return None


def _browser_xyz_connections() -> dict:
    """User's registered XYZ Tile connections from the QGIS Browser panel."""
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
            "_note": "Add any of these with add_xyz_layer(source=<id or name>).",
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
    """The lowercase words of a basemap id or name, punctuation dropped."""
    return {_BASEMAP_SYNONYMS.get(word, word)
            for word in re.split(r"[^0-9a-z]+", (text or "").lower()) if word}


def _resolve_qms_source(catalog: dict, source: str):
    """Resolve a source string to a QMS DataSourceInfo by id, then alias (ci)."""
    if source in catalog:
        return catalog[source], None
    low = source.strip().lower()
    matches = [ds for ds in catalog.values() if (getattr(ds, "alias", "") or "").strip().lower() == low]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        labels = [f"{getattr(d, 'id', '?')} ({getattr(d, 'group', '?')})" for d in matches]
        return None, {"_error": f"Ambiguous basemap '{source}', matches: {', '.join(labels)}. Use the id.",
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
                          f"{', '.join(ds_id for ds_id, _ in sorted(best)[:6])}. Use the id.",
                          "_code": "INVALID_ARGS"}
    return None, None


def _twin_of(layer, candidates) -> object | None:
    """The layer among ``candidates`` reading exactly the same source, if any."""










    if layer is None:
        return None
    try:
        uri = layer.source()
    except Exception:  # noqa: BLE001 - a layer that cannot say what it reads is not a twin
        return None
    for other in candidates:
        if other is None or other is layer:
            continue



        try:
            same = other.source() == uri
        except Exception:  # noqa: BLE001 - a layer that cannot say what it reads is not a twin
            same = False
        if same:
            return other
    return None


def _stacked(layer) -> dict:
    """Put a new basemap over the basemaps already there, and say where it landed."""








    try:
        place_basemap(layer)
        return cover_report(layer)
    except Exception as exc:  # noqa: BLE001 - a layer that is added is added, placed or not
        log_warning(f"Basemap not placed on top of the basemap block: {exc}")
        return {}


def _preset_named(presets: dict, source: str) -> dict | None:
    """A basemap preset addressed by the name it is published under, not its id."""





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
    """The refusal owed for a tile URL this product may not serve, or None."""









    for url in urls:
        host = urllib.parse.urlsplit(str(url or "")).hostname or ""
        reason = net.withdrawn_reason(host)
        if reason:
            return {"_error": reason, "_code": "INVALID_ARGS",
                    "_suggestion": "Call list_xyz_sources and add one of the basemaps it names."}
    return None


_BASEMAP_BRANDS = ("carto", "positron", "stamen", "toner", "mapbox", "google", "bing", "esri", "osm", "openstreetmap")


def _honest_basemap_name(name, ds):
    """The display name the model chose, unless it names a brand the resolved source is not."""






    if not name:
        return name


    own = _basemap_words(f"{getattr(ds, 'id', '')} {getattr(ds, 'alias', '') or ''}")
    words = _basemap_words(str(name))
    for brand in _BASEMAP_BRANDS:
        if brand in words and brand not in own:
            return getattr(ds, "alias", None) or name
    return name


def _chained_preset(source) -> dict | None:
    """The served preset a source names by id or published name when it walks a chain, else None."""






    text = str(source or "").strip()
    if not text or _looks_like_xyz_url(text):
        return None
    try:
        presets = catalog.basemaps()
    except Exception:  # noqa: BLE001 - no catalog is no preset
        return None
    preset = presets.get(text.lower()) or _preset_named(presets, text)
    if preset is None:
        return None
    return preset if preset.get("kind") == "vectortile" or preset.get("fallback") else None


def add_xyz_runs_in_background(args: dict) -> bool:
    """add_xyz_layer's background predicate: only a preset that walks a chain, whose load starts with a fetch."""




    return _chained_preset((args or {}).get("source")) is not None



_PRESET_HOPS = 3



_PROBE_TIMEOUT_S = 8
_PROBE_CACHE_S = 300.0


def _raster_unreachable(preset: dict) -> str:
    """Why a raster preset's server does not serve its zoom 0 tile, or "" when it does."""
    url = str(preset.get("url") or "")
    for key in ("{z}", "{x}", "{y}", "{-y}"):
        url = url.replace(key, "0")
    try:
        body = _http_get(url, timeout=_PROBE_TIMEOUT_S, cache_ttl=_PROBE_CACHE_S)
    except urllib.error.HTTPError as exc:
        return f"its zoom 0 tile answered HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - any failure to answer is a reason to take the fallback
        return net.describe_failure(exc) or str(exc)
    return "" if body else "its zoom 0 tile came back empty"


def _add_preset_chain(preset: dict, name, source) -> dict:
    """Load a preset, and the next preset of its chain when it fails."""








    presets = catalog.basemaps()
    failures: list = []
    seen: set = set()
    current = preset
    while current is not None and current["id"] not in seen and len(seen) < _PRESET_HOPS:
        seen.add(current["id"])
        label = (name if not failures else None) or current["name"]
        if current.get("kind") == "vectortile":
            out = _add_vector_tile_layer({"url": current["url"], "style": current.get("style") or "",
                                          "name": label, "attribution": current.get("attribution") or ""})
        else:
            raster = current
            why = _raster_unreachable(raster) if raster.get("fallback") else ""
            out = {"_error": why} if why else _run_on_main_thread(
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
    return {"_error": "No basemap of this preset's chain could be loaded. " + " ".join(failures),
            "_code": "EXECUTION_FAILED",
            "_suggestion": "Call list_xyz_sources and add another basemap."}


def _add_xyz_layer(args: dict) -> dict:
    source = args["source"]
    name = args.get("name")



    chained = _chained_preset(source)
    if chained is not None:
        return _add_preset_chain(chained, name, source)


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
                kept = twins[0]
                for layer in added:
                    project.removeMapLayer(layer.id())
                if name:
                    kept.setName(name)
                return {
                    "provider": "QuickMapServices",
                    "source_id": getattr(ds, "id", source),
                    "layer_id": kept.id(),
                    "layer_name": kept.name(),
                    "type": getattr(ds, "type", None),
                    "already_present": True,
                    "message": f"'{kept.name()}' already reads this exact source, so it was reused. "
                    "Adding it twice would make the name ambiguous for every later tool.",
                }

            name = _honest_basemap_name(name, ds)
            if name and added:
                added[0].setName(name)
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
                or _build_raw_xyz(conn["url"], name or source, conn.get("zmax", 19), conn.get("zmin", 0), source))


    if _looks_like_xyz_url(source):
        return (_withdrawn_basemap(source)
                or _build_raw_xyz(source, name or "XYZ Tiles", 19, 0, source))


    presets = catalog.basemaps()
    builtin = presets.get(str(source).strip().lower()) or _preset_named(presets, source)
    if builtin is not None:
        return _build_raw_xyz(builtin["url"], name or builtin["name"], builtin["max_zoom"], 0, source,
                              attribution=builtin.get("attribution", ""))


    available = sorted(qms_catalog.keys())[:25] if qms_catalog else list(presets.keys())
    return {
        "_error": f"Unknown basemap '{source}'.",
        "_code": "INVALID_ARGS",
        "_suggestion": "Call list_xyz_sources to see QuickMapServices ids/names.",
        "examples": available,
    }


VECTOR_TILE_EXTENSIONS = (".pbf", ".mvt")

_CACHE_STYLE_S = 3600
_MAX_STYLE_BYTES = 8 * 1024 * 1024


def _fetch_gl_style(url: str):
    """The provider's MapBox GL style as a dict, or (None, reason)."""
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


def _apply_gl_style(layer, style: dict) -> tuple[bool, str]:
    """Convert a MapBox GL style onto a vector tile layer."""





    try:
        from qgis.core import QgsMapBoxGlStyleConversionContext, QgsMapBoxGlStyleConverter
    except ImportError:
        return False, "this QGIS has no MapBox GL style converter"
    converter = QgsMapBoxGlStyleConverter()
    context = QgsMapBoxGlStyleConversionContext()
    try:
        result = converter.convert(style, context)
    except Exception as exc:  # noqa: BLE001 - a style we cannot read is not a failed load
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
    """``(template, zmin, zmax, attribution)`` from a TileJSON document, or ``(None, why, 0, "")``."""










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
    """The collection URL when *url* is an OGC API - Features items endpoint, else None."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    found = _OAPIF_ITEMS_RE.match(parts.path)
    if not found:
        return None
    return f"{parts.scheme}://{parts.netloc}{found.group('collection')}"


def _add_oapif_layer(args: dict) -> dict:
    """Add an OGC API - Features collection through the driver that pages it."""










    url = args.get("url") or ""
    collection = _oapif_collection(url)
    if not collection:
        return {"_error": f"{url} is not an OGC API - Features items endpoint.",
                "code": "INVALID_ARGS",
                "suggestion": "Pass a URL of the shape .../collections/<id>/items."}
    name = args.get("name") or collection.rsplit("/", 1)[-1]

    def _create():
        layer = QgsVectorLayer(f"OAPIF:{collection}", name, "ogr")
        if not layer.isValid():
            return {"_invalid": True}
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(),
                "feature_count": layer.featureCount(), "url": collection,
                "provider": "OGC API - Features"}

    out = _run_on_main_thread(_create, timeout=60)
    if out.get("_invalid"):
        return {"_error": f"The OGC API - Features collection at {collection} would not load.",
                "code": "EXECUTION_FAILED",
                "suggestion": ("Check the collection exists with inspect_data_source. A GDAL older than "
                               "3.0 has no OAPIF driver; the items page still reads a page at a time.")}
    return out


def _add_vector_tile_layer(args: dict) -> dict:
    """A vector tile service: real geometry and attributes, not a picture."""






    url = str(args.get("url") or args.get("source") or "").strip()
    tilejson_note = ""
    tilejson_attribution = ""
    resolved_zoom = None
    if url and not _looks_like_xyz_url(url):

        template, a, b, tilejson_attribution = _resolve_tilejson(url)
        if template is None:




            looks_like_style = "style" in urllib.parse.urlparse(url).path.lower()
            suggestion = ("Pass the tile template, for example "
                          "https://host/tms/1.0.0/LAYER/{z}/{x}/{y}.pbf, or the "
                          "TileJSON URL of the service.")
            if looks_like_style:
                suggestion = ("That address looks like a MapLibre style document. It goes in the style "
                              "argument, with the tile template as the source: source "
                              "https://tiles.openfreemap.org/planet, style "
                              "https://tiles.openfreemap.org/styles/liberty.")
            return {"_error": f"{url} is neither a tile template nor a TileJSON document ({a}).",
                    "_code": "INVALID_ARGS", "_suggestion": suggestion}


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
                "_suggestion": "Pass the tile template, for example "
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
            return {"_error": f"QGIS could not open the vector tile service at {url}.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "Check the template resolves for one tile, and that the zoom range is right."}
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




            out["_note"] = ("No style was passed, so QGIS draws these tiles with its own default "
                            "renderer and the map will look almost empty. Add the service's MapLibre "
                            "style with the style argument, or style the layer by hand.")
        with view_kept():
            QgsProject.instance().addMapLayer(layer)
        out.update(_stacked(layer))
        return out

    return _run_on_main_thread(_create, timeout=60)


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


def _build_raw_xyz(url: str, name: str, zmax, zmin, source_label: str, attribution: str = "") -> dict:
    uri = f"type=xyz&url={encode_uri_url(url)}&zmax={zmax}&zmin={zmin}"
    layer = QgsRasterLayer(uri, name, "wms")
    if not layer.isValid():
        return {"_error": f"Failed to create XYZ layer from: {source_label}"}
    _credit(layer, attribution)




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
    """Write the provider's credit line on the layer, where QGIS shows it."""







    text = (attribution or "").strip()[:500]
    if not text:
        return
    try:
        layer.setAttribution(text)
        metadata = layer.metadata()
        if not metadata.rights():
            metadata.setRights([text])
            layer.setMetadata(metadata)
    except Exception as exc:  # noqa: BLE001 - the layer still draws without its credit line
        log_warning(f"Attribution not set on {layer.name()}: {exc}")
