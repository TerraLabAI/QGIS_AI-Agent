# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Open data portals: search, catalogue parsers and resource picking."""
from __future__ import annotations

import concurrent.futures
import json
import os
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405 - XML input rejects DTD and entities before parsing

from ..core import links, net, tuning
from . import ogc_inspect
from .data_common import (
    _CACHE_CATALOG_S,
    _PORTAL_TIMEOUT,
    _SERVICE_FORMAT_HINTS,
    _SOCRATA_GEOMETRY_TYPES,
    _VECTOR_FORMAT_HINTS,
    _http_get,
    _open_data_portals,
)



_KIND_BY_EXT = {".pmtiles": "pmtiles", ".tif": "cog", ".tiff": "cog", ".jp2": "raster", ".nc": "raster",
                ".laz": "pointcloud", ".las": "pointcloud", ".copc": "pointcloud"}
_FORMAT_EXT = {"geojson": ".geojson", "shp": ".zip", "shapefile": ".zip", "kml": ".kml", "kmz": ".kmz",
               "gpkg": ".gpkg", "geopackage": ".gpkg", "csv": ".csv", "xlsx": ".xlsx", "json": ".json",
               "zip": ".zip", "geotiff": ".tif", "tiff": ".tif", "pmtiles": ".pmtiles", "parquet": ".parquet"}


def _link_file_call(entry: dict) -> dict:
    """The call that opens one file of a listing."""
    if entry.get("folder"):
        return {"tool": "inspect_data_source", "args": {"url": entry["url"]}}
    name = os.path.splitext(entry.get("name") or "")[0] or "dataset"
    if entry.get("service"):
        return {"tool": "add_arcgis_rest_layer", "args": {"url": entry["url"], "name": name}}
    ext = links.extension(entry.get("name") or "") or links.extension(entry["url"])
    if ext in _KIND_BY_EXT:
        return {"tool": "add_data", "args": {"source": entry["url"], "name": name, "kind": _KIND_BY_EXT[ext]}}
    if ext in links.DATA_EXTENSIONS:
        return {"tool": "add_vector_from_url", "args": {"url": entry["url"], "layer_name": name}}
    return {"tool": "fetch_text", "args": {"url": entry["url"]}}


_DEFAULT_FORMAT_PREFERENCE = "geojson"



_FORMAT_PREFERENCE_WORDS = {
    "geojson": "geojson",
    "geo json": "geojson",
    "gpkg": "gpkg",
    "geopackage": "gpkg",
    "geo package": "gpkg",
    "shp": "shp",
    "shapefile": "shp",
    "shape file": "shp",
    "any": "any",
}


_PORTAL_RESULT_CAP = 15


def _portal_by_name(value) -> dict | None:
    """The portal the model named, by its id or by the name we show it."""






    wanted = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    if not wanted:
        return None
    for key, portal in _open_data_portals().items():
        for spelling in (key, portal.get("name", "")):
            if re.sub(r"[^a-z0-9]+", "", str(spelling).lower()) == wanted:
                return portal
    return None


def _normalise_format_preference(value) -> str:
    """The hint the model wrote, read as one of the four formats the search knows."""












    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    if not text:
        return _DEFAULT_FORMAT_PREFERENCE
    first, position = _DEFAULT_FORMAT_PREFERENCE, len(text)
    for word, fmt in _FORMAT_PREFERENCE_WORDS.items():
        found = re.search(rf"\b{word}\b", text)
        if found and found.start() < position:
            first, position = fmt, found.start()
    return first


def _search_open_data(args: dict) -> dict:
    query = args["query"]
    portal_id = args.get("portal")
    portal_url = args.get("portal_url")
    portal_type = args.get("portal_type", "opendatasoft")
    format_pref = _normalise_format_preference(args.get("format_preference"))




    try:
        cap = int(args.get("limit") or _PORTAL_RESULT_CAP)
    except (TypeError, ValueError):
        cap = _PORTAL_RESULT_CAP
    cap = max(1, min(cap, _PORTAL_RESULT_CAP))

    if portal_url:
        custom_portal = _build_custom_portal(portal_url, portal_type)
        if custom_portal is None:
            return {"_error": f"Unsupported portal_type '{portal_type}' for custom portal_url"}
        portals_to_search = [custom_portal]
    elif portal_id:
        portal = _portal_by_name(portal_id)
        if not portal:
            named = ", ".join(f"{key} ({p['name']})" for key, p in _open_data_portals().items())
            return {"_error": f"Unknown portal '{portal_id}'. Available: {named}"}
        portals_to_search = [portal]
    else:
        portals_to_search = list(_open_data_portals().values())

    all_results = []
    errors = []

    def _ask_portal(portal: dict, text: str) -> list:
        if portal["api_type"] == "opendatasoft":
            text = _ods_where(text)
        url = portal["search_url"].format(query=urllib.parse.quote(text))
        budget = tuning.limit("net", "portal_timeout_s", _PORTAL_TIMEOUT)
        parsed = json.loads(_http_get(url, timeout=budget, cache_ttl=_CACHE_CATALOG_S))
        if portal["api_type"] == "data_gouv":
            return _parse_data_gouv_results(parsed, format_pref)
        if portal["api_type"] == "opendatasoft":
            return _parse_opendatasoft_results(parsed, portal["base_url"], format_pref)
        if portal["api_type"] == "ckan":
            return _parse_ckan_results(parsed, format_pref)
        if portal["api_type"] == "socrata":
            return _parse_socrata_results(parsed)
        if portal["api_type"] == "arcgis_hub":
            return _parse_arcgis_hub_results(parsed)
        return []

    def _query_portal(portal: dict) -> tuple[list, str | None]:
        try:
            results = _ask_portal(portal, query)
            asked = query





            if not results:
                for narrower in _portal_narrowings(query):
                    results = _ask_portal(portal, narrower)
                    if results:
                        asked = narrower
                        break

            for r in results:
                r["portal"] = portal["name"]
                if asked != query:
                    r["matched_query"] = asked
            return results, None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            return [], f"{portal['name']}: {e}"
        except Exception as e:  # noqa: BLE001 - an odd payload loses one portal, not the search
            return [], f"{portal['name']}: unexpected answer ({type(e).__name__}: {e})"

    if len(portals_to_search) == 1:
        results, error = _query_portal(portals_to_search[0])
        all_results.extend(results)
        if error:
            errors.append(error)
    else:


        cancel = net.current_cancel_check()

        def _query_portal_in_pool(portal: dict) -> tuple[list, str | None]:
            net.set_cancel_check(cancel)
            try:
                return _query_portal(portal)
            finally:
                net.set_cancel_check(None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(portals_to_search)) as pool:
            for results, error in pool.map(_query_portal_in_pool, portals_to_search):
                all_results.extend(results)
                if error:
                    errors.append(error)

    response = {
        "results": all_results[:cap],
        "count": len(all_results),
        "portals_searched": len(portals_to_search) - len(errors),
        "hint": "Follow each result's import_method and import_arguments. Use inspect_data_source when a service "
        "needs discovery first.",
    }
    narrowed = sorted({r["matched_query"] for r in all_results if r.get("matched_query")})
    if narrowed:
        response["narrowed_to"] = narrowed
        response["narrowed_note"] = (
            "A portal matches every word at once, so the sentence found nothing and the search was narrowed to "
            + ", ".join(repr(n) for n in narrowed)
            + ". The results are about that, not about the rest of the question, so read the titles before loading."
        )
    if errors:
        response["errors"] = errors
    return response


def _portal_text(value, limit: int = 200) -> str:
    """A portal field as text, whatever the portal actually sent."""






    if isinstance(value, dict):
        for key in ("en", "fr", "value", "text"):
            if isinstance(value.get(key), str):
                value = value[key]
                break
        else:
            value = next((v for v in value.values() if isinstance(v, str)), "")
    elif isinstance(value, (list, tuple)):
        value = next((v for v in value if isinstance(v, str)), "")
    return str(value or "")[:limit]





_PORTAL_STOP_WORDS = frozenset({
    "au", "aux", "avec", "dans", "de", "des", "du", "en", "et", "la", "le", "les", "ou", "par",
    "pour", "sur", "un", "une",
    "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with",
    "carte", "couche", "data", "dataset", "donnee", "donnees", "fichier", "layer", "liste",
    "map", "niveau",
})
_PORTAL_NARROW_WIDTHS = (3, 2, 1)


def _portal_terms(query: str) -> list[str]:
    """The words worth searching on, in the order they were written."""





    seen: list[str] = []
    for word in re.findall(r"[^\W\d_]{3,}", str(query or ""), re.UNICODE):
        word = word.lower()
        if word in _PORTAL_STOP_WORDS or word in seen:
            continue
        seen.append(word)
    return seen


def _portal_narrowings(query: str) -> list[str]:
    """Shorter queries to try, widest first, when the sentence found nothing."""
    terms = _portal_terms(query)
    tries: list[str] = []
    for width in _PORTAL_NARROW_WIDTHS:
        if len(terms) > width:
            candidate = " ".join(terms[:width])
            if candidate not in tries:
                tries.append(candidate)
    return tries


def _parse_data_gouv_results(data: dict, format_pref: str) -> list:
    results = []
    for ds in data.get("data", [])[:10]:
        title = _portal_text(ds.get("title"), 300)
        description = _portal_text(ds.get("description"))
        resource = _pick_resource(ds.get("resources", []), format_pref)
        if not resource:
            continue
        url = resource.get("url")
        fmt = (resource.get("format") or "").lower()
        if not url:
            continue
        result = _build_catalog_result(title, description, url, fmt, "data_gouv")




        licence = _portal_text(ds.get("license"), 150)
        if licence and licence != "notspecified":
            result["license"] = licence
        results.append(result)
    return results


def _parse_opendatasoft_results(data: dict, base_url: str, format_pref: str) -> list:
    results = []
    fmt_ext = "geojson" if format_pref in ("geojson", "any") else format_pref

    for ds in data.get("results", [])[:10]:
        dataset_id = ds.get("dataset_id", "")
        metas = ds.get("metas", {}).get("default", {})
        title = _portal_text(metas.get("title"), 300) or dataset_id
        description = _portal_text(metas.get("description"))
        download_url = f"{base_url}/api/explore/v2.1/catalog/datasets/{dataset_id}/exports/{fmt_ext}"
        result = _build_catalog_result(title, description, download_url, fmt_ext, "opendatasoft")
        result["dataset_id"] = dataset_id
        records = metas.get("records_count")
        if isinstance(records, int):
            result["records"] = records




        licence = _portal_text(metas.get("license"), 150)
        if licence:
            result["license"] = licence
        attribution = _portal_text(metas.get("attributions"), 200)
        if attribution:
            result["attribution"] = attribution






        result["area_filter"] = (
            "This URL is the whole dataset. For one area only, append "
            "?where=in_bbox(geo_shape, lat_min, lon_min, lat_max, lon_max) to it."
        )
        results.append(result)
    return results



_HUB_EXPORT_FORMATS = {
    "geojson": "geojson", "shp": "shp", "shapefile": "shp", "kml": "kml", "kmz": "kmz",
    "gpkg": "gpkg", "geopackage": "gpkg", "csv": "csv", "fgb": "fgb", "parquet": "parquet",
    "gpx": "gpx", "xlsx": "xlsx", "xls": "xls",
}
_HUB_FILE_FORMATS = {
    ".geojson": "geojson", ".gpkg": "gpkg", ".shp": "shp", ".kml": "kml", ".kmz": "kmz",
    ".csv": "csv", ".fgb": "fgb", ".gpx": "gpx", ".gml": "gml", ".parquet": "parquet",
    ".xlsx": "xlsx", ".xls": "xls",
}

_HUB_UNREADABLE_FORMATS = {"json_ld", "jsonld", "application/ld+json", "rdf_xml",
                           "rdf_turtle", "n3", "jsonl", "ndjson"}
_HUB_FORMAT_MARKERS = (
    ("wms", "wms"), ("wfs", "wfs"), ("shp", "shp"), ("shape", "shp"),
    ("geojson", "geojson"), ("geo+json", "geojson"), ("geo%2bjson", "geojson"),
    ("gpkg", "gpkg"), ("geopackage", "gpkg"), ("kmz", "kmz"), ("kml", "kml"),
    ("csv", "csv"), ("html", "html"), ("json", "json"),
)
_HUB_LANDING_NAMES = {"about", "about_data", "explore", "information", "overview",
                      "catalog.search", "item.html"}
_HUB_FILE_NAME_PARAMS = ("file", "filename", "file_name")
_HUB_EXPORT_PATH = re.compile(r"/exports/([a-z0-9_]+)$")
_HUB_DOWNLOAD_PATH = re.compile(r"/api/download/v1/items/[^/]+/([a-z0-9_]+)$")


def _hub_declared_format(declared: str) -> str | None:
    """A distribution's declared format, normalised; None when QGIS cannot open it."""
    if declared in _HUB_UNREADABLE_FORMATS:
        return None
    for marker, fmt in _HUB_FORMAT_MARKERS:
        if marker in declared:
            return fmt
    return declared


def _hub_resources(distributions) -> list:
    """The DCAT ``distributions`` of a data.europa.eu dataset, as CKAN resources."""









    if not isinstance(distributions, list):
        return []
    resources = []
    for dist in distributions:
        if not isinstance(dist, dict):
            continue
        url = ""
        from_download = False
        for key in ("download_url", "access_url"):
            value = dist.get(key)
            if isinstance(value, str):
                url = value
            elif isinstance(value, list):
                url = next((v for v in value if isinstance(v, str)), "")
            if url:
                from_download = key == "download_url"
                break
        url = url.strip().rstrip("#").strip()
        if not url.startswith(("http://", "https://")):
            continue
        declared = ""
        raw_format = dist.get("format")
        if isinstance(raw_format, dict):
            for key in ("id", "label"):
                if isinstance(raw_format.get(key), str):
                    declared = raw_format[key]
                    break
        elif isinstance(raw_format, str):
            declared = raw_format
        if not declared and isinstance(dist.get("media_type"), str):
            declared = dist["media_type"]
        declared = declared.lower()
        normalised = _hub_declared_format(declared)

        parts = urllib.parse.urlsplit(url)
        path = parts.path.lower().rstrip("/")
        last = posixpath.basename(path)

        export_name = ""
        for pattern in (_HUB_EXPORT_PATH, _HUB_DOWNLOAD_PATH):
            match = pattern.search(path)
            if match:
                export_name = match.group(1)
                break
        fmt = ""
        if export_name:


            fmt = _HUB_EXPORT_FORMATS.get(export_name, "")
            if not fmt:
                continue
        else:
            ext = links.extension(url)
            if ext in _HUB_FILE_FORMATS:
                fmt = _HUB_FILE_FORMATS[ext]
            elif ext in (".jsonl", ".ndjson"):
                continue
            elif ext in (".zip", ".json"):
                if normalised is None:
                    continue
                fmt = normalised or ext[1:]
            else:
                for name, value in urllib.parse.parse_qsl(parts.query):
                    if name.lower() == "service" and value.lower() in ("wms", "wfs"):
                        fmt = value.lower()
                        break
                if not fmt:
                    for name, value in urllib.parse.parse_qsl(parts.query):
                        if name.lower() in ("format", "f", "outputformat"):
                            query_format = _hub_declared_format(value.lower())
                            if query_format in _HUB_EXPORT_FORMATS.values():
                                fmt = query_format
                                break
                file_drop = False
                if not fmt:
                    for name, value in urllib.parse.parse_qsl(parts.query):
                        if name.lower() not in _HUB_FILE_NAME_PARAMS:
                            continue
                        file_ext = links.extension(value)
                        if file_ext in _HUB_FILE_FORMATS:
                            fmt = _HUB_FILE_FORMATS[file_ext]
                            break
                        if file_ext in (".jsonl", ".ndjson"):
                            file_drop = True
                            break
                        if file_ext in (".zip", ".json"):
                            if normalised is None:
                                file_drop = True
                            else:
                                fmt = normalised or file_ext[1:]
                            break
                if not fmt:
                    for segment in path.split("/"):
                        token = next((t for t in re.split(r"[_.]", segment) if t in ("wms", "wfs")), "")
                        if not token and segment in ("wmsserver", "wfsserver"):
                            token = segment[:3]
                        if token:
                            fmt = token
                            break
                if not fmt and normalised in ("wms", "wfs"):
                    capabilities = any(name.lower() == "request" and value.lower() == "getcapabilities"
                                       for name, value in urllib.parse.parse_qsl(parts.query))
                    if last == "ows" or capabilities:
                        fmt = normalised
                if not fmt and file_drop:
                    continue
        if not fmt:
            if not from_download:


                continue
            if (last in _HUB_LANDING_NAMES or last.endswith((".html", ".htm"))
                    or parts.fragment.startswith("/") or parts.fragment == "overview"
                    or normalised == "html"):
                continue
            if not normalised:
                continue
            fmt = normalised
        resources.append({"format": fmt, "url": url})
    return resources


def _parse_ckan_results(data: dict, format_pref: str) -> list:
    results = []
    datasets = data.get("result", {}).get("results", []) if "result" in data else data.get("results", [])
    if not isinstance(datasets, list):
        return results

    for ds in datasets[:10]:
        if not isinstance(ds, dict):
            continue
        title = _portal_text(ds.get("title"), 300)
        description = _portal_text(ds.get("notes") or ds.get("description"))
        resources = ds.get("resources")
        if not (isinstance(resources, list) and resources):
            resources = _hub_resources(ds.get("distributions"))
        resource = _pick_resource(resources, format_pref)
        if not resource:
            continue
        url = resource.get("url") or resource.get("download_url")
        fmt = (resource.get("format") or "").lower()
        if not url:
            continue
        result = _build_catalog_result(title, description, url, fmt, "ckan")




        licence = _portal_text(ds.get("license_title") or ds.get("license_id"), 150)
        if licence:
            result["license"] = licence
        results.append(result)
    return results


def _parse_socrata_results(data: dict) -> list:
    results = []
    for ds in data.get("results", [])[:10]:
        resource = ds.get("resource", {})
        metadata = ds.get("metadata", {})
        domain = metadata.get("domain")
        dataset_id = resource.get("id")
        if not domain or not dataset_id:
            continue

        datatypes = [str(value).lower() for value in resource.get("columns_datatype", [])]
        is_geospatial = any(any(geom in datatype for geom in _SOCRATA_GEOMETRY_TYPES) for datatype in datatypes)
        if not is_geospatial:
            continue

        title = _portal_text(resource.get("name"), 300) or dataset_id
        description = _portal_text(resource.get("description"))
        download_url = f"https://{domain}/resource/{dataset_id}.geojson?$limit=10000"
        result = _build_catalog_result(title, description, download_url, "geojson", "socrata")
        result["dataset_id"] = dataset_id
        result["landing_page"] = ds.get("link") or ds.get("permalink")
        results.append(result)
    return results


def _parse_arcgis_hub_results(data: dict) -> list:
    """ArcGIS Hub items as layers the plugin can load without a discovery step."""






    results = []
    for item in data.get("data", [])[:10]:
        if not isinstance(item, dict):
            continue
        attributes = item.get("attributes") or {}
        url = _portal_text(attributes.get("url"), 500)
        if not url.startswith("https://"):
            continue
        title = _portal_text(attributes.get("name"), 300) or _portal_text(item.get("id"), 300)
        description = _portal_text(attributes.get("searchDescription"))
        owner = _portal_text(attributes.get("source") or attributes.get("owner"), 120)
        result = {
            "title": title,
            "description": description,
            "format": "arcgis",
            "source_family": "arcgis_hub",
            "service_url": url,
            "import_method": "add_arcgis_rest_layer",
            "import_arguments": {"url": url, "name": title},
        }
        if owner:
            result["publisher"] = owner
        licence = _portal_text(attributes.get("licenseInfo"), 200)
        if licence:
            result["license"] = licence
        count = attributes.get("recordCount")
        if isinstance(count, int):
            result["feature_count"] = count
        item_id = _portal_text(item.get("id"), 120)
        if item_id:
            result["landing_page"] = f"https://hub.arcgis.com/datasets/{item_id}"
        results.append(result)
    return results


def _ods_where(text: str) -> str:
    """The Explore v2.1 catalogue ignores ``q`` and answers with the whole catalogue."""



    words = [w.replace('"', "") for w in str(text or "").split()]
    words = [w for w in words if w]
    return " AND ".join(f'"{w}"' for w in words) or '""'


def _build_custom_portal(portal_url: str, portal_type: str) -> dict | None:
    base = portal_url.rstrip("/")
    if portal_type == "opendatasoft":
        search_url = f"{base}/api/explore/v2.1/catalog/datasets?where={{query}}&limit=10"
    elif portal_type == "ckan":
        search_url = f"{base}/api/3/action/package_search?q={{query}}&rows=10"
    elif portal_type == "socrata":
        if base.endswith("/api/catalog/v1"):
            search_url = f"{base}?q={{query}}&limit=10"
            base = base[: -len("/api/catalog/v1")]
        else:
            search_url = f"{base}/api/catalog/v1?q={{query}}&limit=10"
    else:
        return None
    return {
        "name": base,
        "api_type": portal_type,
        "base_url": base,
        "search_url": search_url,
    }


def _pick_resource(resources: list, format_pref: str) -> dict | None:
    preferred = None
    fallback = None
    for res in resources:
        fmt = (res.get("format") or "").lower()
        if any(marker in fmt for marker in ("jsonl", "ndjson", "json_ld", "jsonld", "ld+json")):
            continue
        url = res.get("url") or res.get("download_url")
        if not url:
            continue
        if format_pref != "any" and format_pref in fmt:
            preferred = res
            break
        if fallback is None and any(marker in fmt for marker in _VECTOR_FORMAT_HINTS | _SERVICE_FORMAT_HINTS):
            fallback = res
    return preferred or fallback


def _build_catalog_result(title: str, description: str, url: str, fmt: str, source_family: str) -> dict:
    result = {
        "title": title,
        "description": description,
        "format": fmt,
        "source_family": source_family,
    }
    result.update(_build_import_strategy(url, title, fmt))
    return result


def _build_direct_import_result(url: str, title: str, source_family: str, fmt: str) -> dict:
    return {
        "source_family": source_family,
        "final_url": url,
        "format": fmt,
        "import_method": "add_vector_from_url",
        "import_arguments": {"url": url, "layer_name": os.path.splitext(title)[0] or title},
    }


def _build_import_strategy(url: str, title: str, fmt: str) -> dict:
    """The next call and its arguments; the sentence that routes to it is the server's."""
    fmt_lower = (fmt or "").lower()
    if "wms" in fmt_lower or "wfs" in fmt_lower:
        return {
            "service_url": url,
            "import_method": "inspect_data_source",
            "import_arguments": {"url": url},
        }
    return {
        "download_url": url,
        "import_method": "add_vector_from_url",
        "import_arguments": {"url": url, "layer_name": title},
    }


_MAX_DISTRIBUTIONS = 10


def _entry_for(name: str, url: str, fmt: str = "", size=None) -> dict | None:
    """One file named by a page or an API, with the call that opens it; None when it is not data."""
    url = links.resolve(url).url if url.startswith(("http://", "https://")) else ""
    if not url:
        return None
    entry = {"name": str(name or os.path.basename(urllib.parse.urlsplit(url).path) or "file")[:120], "url": url}
    ext = links.extension(entry["name"]) or links.extension(url) or _FORMAT_EXT.get(str(fmt or "").lower(), "")
    if ext not in links.DATA_EXTENSIONS:
        return None
    if not links.extension(entry["name"]):
        entry["name"] += ext
    if fmt:
        entry["format"] = str(fmt)[:40]
    if isinstance(size, int) and size > 0:
        entry["size_bytes"] = size
    entry["add"] = _link_file_call(entry)
    return entry


def _ranked(entries: list) -> list:
    seen, out = set(), []
    for entry in sorted((e for e in entries if e), key=links.rank):
        if entry["url"] not in seen:
            seen.add(entry["url"])
            out.append(entry)
    return out[:_MAX_DISTRIBUTIONS]


def _json_download_links(payload) -> list:
    """Files an API answer names by URL (geoBoundaries' gjDownloadURL), each with its call."""
    record = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else payload
    if not isinstance(record, dict):
        return []
    return _ranked([_entry_for(key, value) for key, value in record.items()
                    if isinstance(value, str) and value.startswith(("http://", "https://"))])


_JSONLD_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ANCHOR_TAG_RE = re.compile(r"<a\b[^>]*href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r"([a-zA-Z:-]+)\s*=\s*[\"']([^\"']*)[\"']")
_DATA_TYPES = ("application/geo+json", "application/vnd.geo+json", "application/json", "text/csv",
               "application/vnd.google-earth.kml+xml", "application/vnd.google-earth.kmz",
               "application/geopackage+sqlite3", "application/zip", "image/tiff", "application/x-shapefile")
_RDF_RE = re.compile(r"<dcat:(?:downloadURL|accessURL)[^>]*rdf:resource=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _page_distributions(final_url: str, page: str) -> list:
    """The data files a dataset page names, geospatial first, each with the call that opens it."""




    import html as _html

    page = page[:3_000_000]
    entries: list = []
    for block in _JSONLD_RE.findall(page):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else data.get("@graph") or [data] if isinstance(data, dict) else []
        for node in nodes:
            if not isinstance(node, dict) or "Dataset" not in str(node.get("@type")):
                continue
            distributions = node.get("distribution") or []
            for dist in distributions if isinstance(distributions, list) else [distributions]:
                if isinstance(dist, dict):
                    url = str(dist.get("contentUrl") or dist.get("url") or "")
                    fmt = str(dist.get("encodingFormat") or dist.get("fileFormat") or "")
                    short = fmt.rsplit("/", 1)[-1].replace("vnd.google-earth.", "").replace("+xml", "")
                    entries.append(_entry_for(str(dist.get("name") or ""), urllib.parse.urljoin(final_url, url),
                                              {"geo+json": "geojson", "vnd.geo+json": "geojson"}.get(short, short)))
    for url in _RDF_RE.findall(page):
        entries.append(_entry_for("", urllib.parse.urljoin(final_url, _html.unescape(url))))
    for tag in _LINK_TAG_RE.findall(page):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        if "alternate" in attrs.get("rel", "").lower() and attrs.get("type", "").lower() in _DATA_TYPES \
                and not attrs.get("hreflang"):
            fmt = attrs["type"].lower().rsplit("/", 1)[-1]
            entries.append(_entry_for(attrs.get("title", ""), urllib.parse.urljoin(final_url, _html.unescape(
                attrs.get("href", ""))), {"geo+json": "geojson", "vnd.geo+json": "geojson"}.get(fmt, fmt)))
    for href, text in _ANCHOR_TAG_RE.findall(page):
        url = urllib.parse.urljoin(final_url, _html.unescape(href.strip()))
        label = " ".join(re.sub(r"<[^>]+>", " ", text).split())
        entries.append(_entry_for(label if links.extension(label) else "", url))
    return _ranked(entries)


def _inspect_json_payload(final_url: str, payload: dict) -> dict:
    if not isinstance(payload, dict):


        found = _json_download_links(payload)
        if found:
            return {"source_family": "json_api", "final_url": final_url, "layers": found,
                    "import_method": found[0]["add"]["tool"], "import_arguments": found[0]["add"]["args"],
                    "message": ("A JSON answer that names files to download: each entry in layers carries the "
                                "call that loads it, GeoJSON first.")}
        return {"source_family": "json_api", "final_url": final_url,
                "message": "JSON source detected, but no direct import strategy was inferred."}
    if payload.get("type") == "FeatureCollection":
        title = os.path.basename(urllib.parse.urlparse(final_url).path) or "dataset"
        return _build_direct_import_result(final_url, title, "geojson_feature_collection", "geojson")

    if "stac_version" in payload and payload.get("type") == "Feature":
        return {"source_family": "stac_item", "final_url": final_url, "stac_version": payload.get("stac_version"),
                "item_id": payload.get("id"), "collection": payload.get("collection"),
                "assets": sorted((payload.get("assets") or {}).keys())[:20],
                "import_method": "add_stac_layer", "import_arguments": {"item_url": final_url},
                "message": "A STAC item: add_stac_layer streams its visual asset, or the one named in asset."}
    if "stac_version" in payload and payload.get("type") == "Collection":
        items = next((link.get("href") for link in payload.get("links") or []
                      if isinstance(link, dict) and link.get("rel") == "items"), "") or final_url.rstrip("/") + "/items"
        return {"source_family": "stac_collection", "final_url": final_url, "collection": payload.get("id"),
                "title": payload.get("title"), "items_url": items,
                "next_call": {"tool": "fetch_json",
                              "args": {"url": items + ("&" if "?" in items else "?") + "limit=5"}},
                "message": ("A STAC collection: it holds items, not one image. Read a few items with next_call "
                            "(add bbox and datetime to the items URL), then add_stac_layer with an item's URL.")}
    if "stac_version" in payload:
        return {
            "source_family": "stac_api",
            "final_url": final_url,
            "stac_version": payload.get("stac_version"),
            "collections": _extract_collection_ids(payload),
            "message": "STAC API detected. Use a STAC-aware client or browse collections/items next.",
        }


    service = ogc_inspect.json_service(final_url, payload)
    if service is not None:
        return service

    if isinstance(payload.get("collections"), list):


        source_family = "stac_api" if ogc_inspect.is_stac_collections(payload) else "ogc_api"
        return {
            "source_family": source_family,
            "final_url": final_url,
            "collections": _extract_collection_ids(payload),
            "message": "Collection API detected. Browse a collection items endpoint or use a dedicated connector.",
        }

    if isinstance(payload.get("result"), dict) and isinstance(payload["result"].get("results"), list):
        return {
            "source_family": "ckan_catalog",
            "final_url": final_url,
            "dataset_count": len(payload["result"].get("results", [])),
            "message": "CKAN catalog detected. search_open_data can query CKAN portals with portal_type='ckan'.",
        }

    if isinstance(payload.get("results"), list) and payload["results"]:
        first = payload["results"][0]
        if "dataset_id" in first:
            return {
                "source_family": "opendatasoft_catalog",
                "final_url": final_url,
                "dataset_count": len(payload["results"]),
                "message": "OpenDataSoft catalog detected. search_open_data can query this portal directly.",
            }
        if "resource" in first:
            return {
                "source_family": "socrata_catalog",
                "final_url": final_url,
                "dataset_count": len(payload["results"]),
                "message": "Socrata catalog detected. search_open_data can query this portal with "
                "portal_type='socrata'.",
            }

    if isinstance(payload.get("data"), list):
        return {
            "source_family": "data_gouv_catalog",
            "final_url": final_url,
            "dataset_count": len(payload["data"]),
            "message": "data.gouv.fr catalog response detected.",
        }

    if isinstance(payload.get("tiles"), list) and "VectorTileServer" in final_url:
        base = final_url.split("?", 1)[0].rstrip("/")
        template = str(payload["tiles"][0])
        template = template if template.startswith("http") else f"{base}/{template}"
        styles = str(payload.get("defaultStyles") or "resources/styles")
        return {"source_family": "arcgis_vectortile", "final_url": final_url, "name": payload.get("name"),
                "import_method": "add_data",
                "import_arguments": {"source": template, "kind": "vectortile", "style": f"{base}/{styles}/root.json"},
                "message": "An ArcGIS vector tile service: it loads as a vector tile layer with its own style."}
    found = _json_download_links(payload)
    if found:
        out = {"source_family": "json_api", "final_url": final_url, "layers": found,
               "message": ("A JSON answer that names files to download: each entry in layers carries the call "
                           "that loads it, GeoJSON first.")}
        out["import_method"] = found[0]["add"]["tool"]
        out["import_arguments"] = found[0]["add"]["args"]
        return out
    return {
        "source_family": "json_api",
        "final_url": final_url,
        "message": "JSON source detected, but no direct import strategy was inferred.",
    }


def _inspect_xml_payload(final_url: str, payload_text: str) -> dict:
    upper = payload_text.upper()
    if "<!ENTITY" in upper:
        return {
            "source_family": "xml",
            "final_url": final_url,
            "message": "XML that declares entities is not read.",
        }
    del upper


    payload_text = ogc_inspect.strip_doctype(payload_text)
    if len(payload_text) > _XML_TREE_CAP:
        streamed = _stream_capabilities(final_url, payload_text)
        if streamed is not None:
            return streamed
        return {
            "source_family": "xml",
            "final_url": final_url,
            "message": "XML payload too large to parse safely (>5MB).",
        }
    try:
        root = ET.fromstring(payload_text)  # nosec B314 - DTD and entities rejected above
    except ET.ParseError:
        return {
            "source_family": "xml",
            "final_url": final_url,
            "message": "XML source detected, but it could not be parsed safely.",
        }

    root_name = root.tag.split("}", 1)[-1].lower()
    if root_name == "featurecollection":
        return _wfs_hits_result(final_url, root.attrib)

    service = ogc_inspect.xml_capabilities(final_url, root, payload_text)
    if service is not None:
        return service
    listing = _inspect_object_listing(final_url, root, root_name)
    if listing is not None:
        return listing
    return {
        "source_family": "xml",
        "final_url": final_url,
        "root_element": root_name,
        "message": ("XML source detected, but it is neither a service description (WMS, WMTS, WCS, WFS) nor a "
                    "bucket listing."),
    }







_XML_TREE_CAP = 5_000_000
_STREAM_CHUNK = 1 << 20


def _stream_capabilities(final_url: str, payload_text: str) -> dict | None:
    """The same capabilities answer, read without building the tree."""




    parser = ET.XMLPullParser(("start", "end"))
    root_name = ""
    names: list[str] = []
    wanted = ogc_inspect.MAX_LAYERS + ogc_inspect.MAX_OTHER_NAMES
    try:
        for start in range(0, len(payload_text), _STREAM_CHUNK):
            parser.feed(payload_text[start:start + _STREAM_CHUNK])
            for event, element in parser.read_events():
                tag = element.tag.split("}", 1)[-1]
                if event == "start":
                    if not root_name:
                        root_name = tag.lower()
                        if root_name not in ("wms_capabilities", "wmt_ms_capabilities", "wfs_capabilities"):
                            return None
                    continue
                if tag == "Name" and element.text:
                    text = element.text.strip()
                    if text and text not in names:
                        names.append(text)
                element.clear()
            if len(names) >= wanted:
                break
    except ET.ParseError:
        if not names:
            return None
    family = "wfs_capabilities" if root_name == "wfs_capabilities" else "wms_capabilities"
    return ogc_inspect.names_listing(family, final_url, names[:wanted])


def _wfs_hits_result(final_url: str, attrib: dict) -> dict:
    """What a ``RESULTTYPE=hits`` request answers: the count, and no features."""





    matched = attrib.get("numberMatched") or attrib.get("numberOfFeatures")
    returned = attrib.get("numberReturned")
    out = {
        "source_family": "wfs_feature_count",
        "final_url": final_url,

        "service_url": ogc_inspect.service_base(final_url),
        "import_method": "add_wfs_layer",
        "import_arguments": {"url": ogc_inspect.service_base(final_url)},
    }
    if matched not in (None, "", "unknown"):
        out["feature_count"] = matched
        out["message"] = (f"The service reports {matched} features for this request. Load them with "
                          "add_wfs_layer, and pass a bounding box to keep the download to the view.")
    else:
        out["message"] = ("A WFS feature collection, with no count in it. Repeat the request with "
                          "RESULTTYPE=hits to get one, or load it with add_wfs_layer.")
    if returned not in (None, ""):
        out["features_returned"] = returned
    return out







_LISTING_MAX_FOLDERS = 40
_LISTING_MAX_FILES = 40


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child_text(element, name: str) -> str:
    for child in element:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _inspect_object_listing(final_url: str, root, root_name: str) -> dict | None:
    """An S3 or Azure Blob listing read as a directory, or None when it is neither."""








    if root_name == "listbucketresult":
        store = "s3"
        prefix = _child_text(root, "Prefix")
        token = _child_text(root, "NextContinuationToken") or _child_text(root, "NextMarker")
        folders = [_child_text(el, "Prefix") for el in root if _local(el.tag) == "CommonPrefixes"]
        files = [(_child_text(el, "Key"), _child_text(el, "Size"))
                 for el in root if _local(el.tag) == "Contents"]
        container = _child_text(root, "Name")
    elif root_name == "enumerationresults":
        store = "azure_blob"
        prefix = _child_text(root, "Prefix")
        token = _child_text(root, "NextMarker")
        blobs = next((el for el in root if _local(el.tag) == "Blobs"), None)
        folders = [_child_text(el, "Name") for el in (blobs or []) if _local(el.tag) == "BlobPrefix"]
        files = []
        for el in (blobs or []):
            if _local(el.tag) != "Blob":
                continue
            properties = next((c for c in el if _local(c.tag) == "Properties"), None)
            size = _child_text(properties, "Content-Length") if properties is not None else ""
            files.append((_child_text(el, "Name"), size))
        container = (root.get("ContainerName") or "").rsplit("/", 1)[-1]
    else:
        return None

    base = _strip_query_string(final_url).rstrip("/")
    listed = []
    for key, size in files[:_LISTING_MAX_FILES]:
        if not key:
            continue


        entry = {"key": key, "url": f"{base}/{urllib.parse.quote(key, safe='/~')}"}
        if size.isdigit():
            entry["size_bytes"] = int(size)
        kind = deduce_listing_kind(key)
        if kind:
            entry["kind"] = kind
        listed.append(entry)

    out = {
        "source_family": "object_listing",
        "final_url": final_url,
        "store": store,
        "folders": [f for f in folders[:_LISTING_MAX_FOLDERS] if f],
        "files": listed,
        "message": ("A bucket listing, not a dataset. Each folder is a prefix to list next, and a file whose "
                    "entry carries a kind loads with add_data using that url and kind."),
    }
    if container:
        out["container"] = container
    if prefix:
        out["prefix"] = prefix
    if len(folders) > _LISTING_MAX_FOLDERS or len(files) > _LISTING_MAX_FILES:
        out["listing_truncated"] = True
        out["message"] += (f" This page held {len(folders)} folders and {len(files)} keys and only the first "
                           "forty of each are above: narrow the prefix rather than reading this as the whole "
                           "list.")
    if token:
        out["next_page"] = token
        out["message"] += (" More entries exist: repeat the call with continuation-token=<next_page> on S3, "
                           "marker=<next_page> on Azure.")
    return out


def deduce_listing_kind(key: str) -> str:
    """The add_data kind for a key in a listing, or "" when it is not a layer file."""
    from .layer_io_tools import point_cloud_provider
    lower = key.lower()
    if point_cloud_provider(lower):
        return "pointcloud"
    if lower.endswith(".pmtiles"):
        return "pmtiles"
    if lower.endswith((".tif", ".tiff")):
        return "cog"
    if lower.endswith((".fgb", ".parquet", ".geoparquet", ".gpkg", ".geojson", ".shp", ".zip")):
        return "vector"
    if lower.endswith("ept.json"):
        return "pointcloud"
    return ""


def _extract_collection_ids(payload: dict) -> list[str]:
    collections = payload.get("collections", [])
    ids = []
    for collection in collections[:20]:
        identifier = collection.get("id") or collection.get("name")
        if identifier:
            ids.append(str(identifier))
    return ids


def _strip_query_string(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
