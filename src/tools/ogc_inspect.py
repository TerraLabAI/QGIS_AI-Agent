# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a map service says about itself, read into the call that loads each layer."""









from __future__ import annotations

import html
import json
import re
import urllib.parse





MAX_LAYERS = 12
MAX_OTHER_NAMES = 50
_OTHER_TITLE_CHARS = 50
_TITLE_CHARS = 80
_CRS_SHOWN = 8
_FORMATS_SHOWN = 6




_OGC_KEYS = frozenset({"service", "request", "version", "acceptversions", "sections", "updatesequence",
                       "acceptformats", "f"})








_REQUEST_KEYS = frozenset({
    "layer", "layers", "styles", "style", "format", "width", "height", "bbox", "crs", "srs", "transparent",
    "bgcolor", "sld", "sld_body", "sld_version", "time", "elevation", "exceptions", "dpi", "map_resolution",
    "format_options", "tiled", "tilesorigin", "buffer",
    "legend_options", "scale", "rule", "language",
    "query_layers", "info_format", "feature_count", "i", "j", "x", "y",
    "typename", "typenames", "count", "maxfeatures", "startindex", "outputformat", "srsname", "resulttype",
    "propertyname", "featureid", "resourceid", "filter", "cql_filter", "sortby", "storedquery_id", "namespaces",
    "namespace", "valuereference",
    "tilematrixset", "tilematrix", "tilerow", "tilecol",
    "coverageid", "coverage", "identifier", "identifiers", "subset", "subsettingcrs", "outputcrs", "scalefactor",
    "scalesize", "resx", "resy", "interpolation", "rangesubset", "mediatype",
})

_REQUEST_SERVICE = {
    "getmap": "WMS", "getlegendgraphic": "WMS", "getfeatureinfo": "WMS", "describelayer": "WMS", "getstyles": "WMS",
    "getfeature": "WFS", "describefeaturetype": "WFS", "getpropertyvalue": "WFS", "liststoredqueries": "WFS",
    "describestoredqueries": "WFS",
    "gettile": "WMTS",
    "getcoverage": "WCS", "describecoverage": "WCS",
}

_REQUEST_LAYER_KEYS = {
    "WMS": ("layers", "layer", "query_layers"),
    "WFS": ("typenames", "typename"),
    "WMTS": ("layer",),
    "WCS": ("coverageid", "coverage", "identifiers", "identifier"),
}
_SERVICE_KIND = {"WMS": "wms", "WMTS": "wms", "WFS": "wfs", "WCS": "wcs"}



RECOGNIZED_FAMILIES = frozenset({
    "wms_capabilities", "wmts_capabilities", "wcs_capabilities", "wfs_capabilities",
    "arcgis_services", "arcgis_mapserver", "arcgis_featureserver", "arcgis_imageserver", "arcgis_layer",
    "ogc_api_collections", "ogc_api_tilesets",
})

_PICTURES_NOT_VALUES = ("A WMS or WMTS layer is a picture: it cannot give heights or values. For those use a "
                        "WCS coverage (or a DEM file) of the same data.")


def _local(tag: str) -> str:
    return str(tag).split("}", 1)[-1]


def _namespace(tag: str) -> str:
    text = str(tag)
    return text[1:].split("}", 1)[0].lower() if text.startswith("{") else ""


def _text(element) -> str:
    return " ".join((element.text or "").split()) if element is not None else ""


def _child(element, name: str):
    for child in element if element is not None else ():
        if _local(child.tag) == name:
            return child
    return None


def _children(element, name: str) -> list:
    return [child for child in (element if element is not None else ()) if _local(child.tag) == name]


def _find(element, *path: str):
    for name in path:
        element = _child(element, name)
        if element is None:
            return None
    return element


def _short(text: str, limit: int = _TITLE_CHARS) -> str:
    text = " ".join(html.unescape(str(text or "")).split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _href(element) -> str:
    if element is None:
        return ""
    return (element.get("{http://www.w3.org/1999/xlink}href") or element.get("href")
            or element.get("onlineResource") or "").strip()


def _unique(values) -> list:
    out: list = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out







_NAMED_CRS = {
    "urn:ogc:def:crs:ogc:1.3:crs84": "OGC:CRS84",
    "urn:ogc:def:crs:ogc::crs84": "OGC:CRS84",
    "http://www.opengis.net/def/crs/ogc/1.3/crs84": "OGC:CRS84",
    "crs84": "OGC:CRS84",
    "crs:84": "OGC:CRS84",
}


_EPSG_RE = re.compile(r"(\d{4,6})\s*$")

_WEB_MERCATOR_ALIASES = frozenset({"102100", "102113", "900913"})


def epsg_of(supported_crs: str) -> str:
    """``EPSG:<code>`` (or a named CRS) out of any form a CRS is written in, else ""."""
    text = (supported_crs or "").strip()
    named = _NAMED_CRS.get(text.lower())
    if named:
        return named
    found = _EPSG_RE.search(text)
    if not found:
        return ""
    code = found.group(1)
    return "EPSG:3857" if code in _WEB_MERCATOR_ALIASES else f"EPSG:{code}"


def _pick_crs(crs_list: list) -> str:
    """Web Mercator when the layer is served in it, the project's usual CRS; then WGS84; then the first."""
    for wanted in ("EPSG:3857", "EPSG:4326"):
        if wanted in crs_list:
            return wanted
    return next((crs for crs in crs_list if crs.startswith("EPSG:")), "")




def service_base(url: str) -> str:
    """The endpoint of a service: the description's and a request's own query keys taken out."""




    parts = urllib.parse.urlsplit(str(url or "").strip())


    kept = []
    for pair in parts.query.split("&"):
        key = urllib.parse.unquote_plus(pair.split("=", 1)[0]).strip().lower()
        if key and key not in _OGC_KEYS and key not in _REQUEST_KEYS:
            kept.append(pair)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), ""))


def with_path(url: str, suffix: str) -> str:
    """*url* with *suffix* appended to its path, its query kept after it."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + suffix, parts.query, ""))


def ogc_request(url: str) -> dict | None:
    """What an OGC request URL asks for, or None when the URL is not one."""





    parts = urllib.parse.urlsplit(str(url or "").strip())
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    lowered = {}
    for key, value in query:
        lowered.setdefault(key.lower(), value)
    request = lowered.get("request", "").strip()
    if not request or request.lower() == "getcapabilities":
        return None
    named = lowered.get("service", "").strip().upper()
    service = named if named in _SERVICE_KIND else _REQUEST_SERVICE.get(request.lower(), "")
    if not service:
        return None
    layers: list[str] = []
    for key in _REQUEST_LAYER_KEYS[service]:
        for value in lowered.get(key, "").split(","):
            if value.strip() and value.strip() not in layers:
                layers.append(value.strip())
        if layers:
            break
    endpoint = service_base(url)
    return {"service": service, "request": request, "layers": layers, "endpoint": endpoint,
            "capabilities": with_query(endpoint, {"SERVICE": service, "REQUEST": "GetCapabilities"})}


def _same_layer(wanted: str, name: str) -> bool:
    wanted, name = wanted.strip().lower(), str(name or "").strip().lower()
    return bool(wanted) and (wanted == name or wanted.split(":")[-1] == name.split(":")[-1] and (
        ":" not in wanted or ":" not in name))


def attach_layer_in_link(answer: dict, pasted: str, request: dict) -> dict:
    """The capabilities listing with the layer the pasted request named put first, ready to add."""






    out = dict(answer)
    out["pasted_request"] = request["request"]
    out["pasted_url"] = pasted
    service, endpoint = request["service"], request["endpoint"]
    kind = _SERVICE_KIND[service]
    if not request["layers"]:
        out["message"] = (f"The link is a {service} {request['request']} request, not the service itself. "
                          + str(answer.get("message") or ""))
        return out
    wanted = request["layers"][0] if service != "WMS" else ",".join(request["layers"])
    first = request["layers"][0]
    layers = list(out.get("layers") or [])
    found = next((entry for entry in layers if _same_layer(first, entry.get("name", ""))), None)
    listed_other = any(_same_layer(first, str(item).split(": ", 1)[0]) for item in out.get("other_layers") or [])
    if found is not None:
        layers.remove(found)
        layers.insert(0, found)
        out["layers"] = layers
        entry = dict(found)
        if service == "WMS" and len(request["layers"]) > 1:
            entry["add"] = {"tool": "add_data", "args": dict(found["add"]["args"], layer=wanted)}
    else:
        args = {"source": (out.get("service_url") or endpoint), "kind": kind, "layer": wanted}
        entry = {"name": wanted, "add": {"tool": "add_data", "args": args}}
    out["layer_in_link"] = entry
    out["import_method"] = entry["add"]["tool"]
    out["import_arguments"] = entry["add"]["args"]
    listed = found is not None or listed_other
    recognized = out.get("source_family") in RECOGNIZED_FAMILIES
    said = (f"The link is a {service} {request['request']} request for {wanted}, not the service itself; "
            f"layer_in_link carries the add_data call that loads that layer from the service, with every "
            "access parameter the link carried.")
    partial = bool(out.get("truncated")) or len(out.get("other_layers") or []) >= MAX_OTHER_NAMES
    if recognized and not listed and partial:
        said += (" The listing below is cut before this layer's name, so the call uses the name the link "
                 "carries.")
    elif recognized and not listed:
        said += (" The service's capabilities do not list this layer to this request: if the link carried a key, "
                 "it is in the call already; otherwise the layer may need an account.")
    elif not recognized:
        said += " The service did not describe itself, so the layer name is the link's and unchecked."
    bbox = next((value for key, value in urllib.parse.parse_qsl(urllib.parse.urlsplit(pasted).query,
                                                                 keep_blank_values=True)
                 if key.lower() == "bbox" and value), "")
    if bbox:


        out["bbox_in_link"] = bbox
        said += (" bbox_in_link is the area the link asked for, in the CRS it names (EPSG:4326 in a WFS 2.0 "
                 "or WMS 1.3.0 link is lat,lon): zoom there before loading, or pass it as a filter.")
    if service == "WFS" and request["request"].lower() == "getfeature":
        out["direct_download"] = {"tool": "add_vector_from_url", "args": {"url": pasted}}
        said += (" The link itself is a feature download: direct_download loads exactly what it asks for as "
                 "a file, while layer_in_link streams the layer from the service.")
    out["message"] = said + (" " + str(answer.get("message")) if answer.get("message") and recognized else "")
    return out


def with_query(url: str, params: dict) -> str:
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


_with_query = with_query


def document_endpoint(root) -> str:
    """The endpoint a pasted capabilities document names for itself, else ""."""
    for element in root.iter():
        if _local(element.tag) == "ServiceMetadataURL" and _href(element):
            return _href(element)
    for element in root.iter():
        if _local(element.tag) == "Get":
            href = _href(element) or _href(_child(element, "OnlineResource"))
            if href:
                return href
    return ""


def _endpoint(final_url: str, root) -> str:
    return service_base(final_url or document_endpoint(root))


def capabilities_probes(url: str, html_answer: bool = False) -> list[str]:
    """The capabilities requests worth sending to an endpoint that did not describe itself."""





    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    request = ogc_request(url)
    if request is not None:
        return [request["capabilities"]]
    if any(key.lower() in ("request", "f") for key, _ in query):
        return []
    base = service_base(url)
    lower = parts.path.lower() + " " + parts.query.lower()
    named = next((value.upper() for key, value in query if key.lower() == "service"), "")
    if named in ("WMS", "WMTS", "WCS", "WFS"):
        kinds = [named]
    else:
        kinds = [kind for hint, kind in (("wmts", "WMTS"), ("wcs", "WCS"), ("wfs", "WFS"), ("wms", "WMS"))
                 if hint in lower]
    probes = []
    if not kinds:
        if html_answer:

            probes.append(_with_query(base, {"f": "json"}))
        kinds = ["WMS", "WFS", "WCS", "WMTS"]
    probes += [_with_query(base, {"SERVICE": kind, "REQUEST": "GetCapabilities"}) for kind in kinds]
    return probes[:4]


_ARCGIS_RE = re.compile(r"/(?:MapServer|FeatureServer|ImageServer|VectorTileServer)(?:/\d+)?/?$"
                        r"|/rest/services(?:/[^?]*)?$", re.IGNORECASE)


def arcgis_json_url(url: str) -> str:
    """An ArcGIS REST address asked for its JSON: bare, it answers an HTML page."""
    parts = urllib.parse.urlsplit(url)
    if not _ARCGIS_RE.search(parts.path):
        return url
    if any(key.lower() == "f" for key, _ in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)):
        return url
    return _with_query(url, {"f": "json"})











_WMTS_LAYER_RE = re.compile(rb"<(?:wmts:)?Layer>(.*?)</(?:wmts:)?Layer>", re.DOTALL)
_WMTS_IDENTIFIER_RE = re.compile(rb"<ows:Identifier>\s*([^<\s][^<]*?)\s*</ows:Identifier>")
_WMTS_TITLE_RE = re.compile(rb"<ows:Title[^>]*>\s*([^<]*?)\s*</ows:Title>")
_WMTS_SET_RE = re.compile(rb"<TileMatrixSet>\s*([^<\s][^<]*?)\s*</TileMatrixSet>")
_WMTS_FORMAT_RE = re.compile(rb"<Format>\s*([^<\s][^<]*?)\s*</Format>")
_WMTS_STYLE_RE = re.compile(rb"<Style[^>]*>.*?<ows:Identifier>\s*([^<\s][^<]*?)\s*</ows:Identifier>", re.DOTALL)
_WMTS_SET_DEF_RE = re.compile(
    rb"<TileMatrixSet>\s*<ows:Identifier>\s*([^<\s][^<]*?)\s*</ows:Identifier>.*?"
    rb"<ows:SupportedCRS>\s*([^<\s][^<]*?)\s*</ows:SupportedCRS>", re.DOTALL)


def wmts_layers(body: bytes) -> dict[str, dict]:
    """``{layer id: {title, sets, formats, styles, crs}}`` from a WMTS capabilities body, empty when none."""
    body = body or b""
    crs_of = {name.decode("utf-8", "replace"): crs.decode("utf-8", "replace")
              for name, crs in _WMTS_SET_DEF_RE.findall(body)}
    out: dict[str, dict] = {}
    for block in _WMTS_LAYER_RE.findall(body):
        found = _WMTS_IDENTIFIER_RE.search(block)
        if not found:
            continue
        layer_id = found.group(1).decode("utf-8", "replace")
        title = _WMTS_TITLE_RE.search(block.split(b"<Style", 1)[0])
        sets = [name.decode("utf-8", "replace") for name in _WMTS_SET_RE.findall(block)]
        out[layer_id] = {
            "title": html.unescape(title.group(1).decode("utf-8", "replace")) if title else "",
            "sets": sets,
            "formats": [f.decode("utf-8", "replace") for f in _WMTS_FORMAT_RE.findall(block)],
            "styles": [st.decode("utf-8", "replace") for st in _WMTS_STYLE_RE.findall(block)],
            "crs": {name: crs_of.get(name, "") for name in sets},
        }
    return out


def wmts_capabilities_url(url: str) -> str:
    """The GetCapabilities address of a WMTS, from whatever form of its URL was given."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    if any(key.lower() == "service" for key in query) or parts.path.lower().endswith(".xml"):
        return url
    return _with_query(url, {"SERVICE": "WMTS", "REQUEST": "GetCapabilities", "VERSION": "1.0.0"})


def _wmts(final_url: str, root, text: str) -> dict:
    endpoint = final_url if urllib.parse.urlsplit(final_url).path.lower().endswith(".xml") \
        else _endpoint(final_url, root)
    source = endpoint if endpoint.lower().endswith(".xml") else _with_query(
        endpoint, {"SERVICE": "WMTS", "REQUEST": "GetCapabilities"})
    described = wmts_layers(text.encode("utf-8"))
    entries = []
    for layer_id, row in list(described.items())[:MAX_LAYERS]:
        served = _unique(epsg_of(row["crs"].get(name, "")) for name in row["sets"])
        entries.append({
            "name": layer_id,
            **({"title": _short(row["title"])} if row["title"] else {}),

            "crs": sorted(served, key=lambda crs: crs != "EPSG:3857")[:4],
            "add": {"tool": "add_data", "args": {"source": source, "kind": "wms", "layer": layer_id}},
        })
    formats = _unique(f for row in described.values() for f in row["formats"])
    return _listing("wmts_capabilities", "WMTS", final_url, endpoint, root, entries,
                    [(layer_id, row["title"]) for layer_id, row in described.items()],
                    formats=formats,
                    message=("WMTS capabilities read. Each layer carries the add_data call that loads it; the "
                             "tile matrix set is chosen from the layer's own, Web Mercator first. "
                             + _PICTURES_NOT_VALUES))




def _wms(final_url: str, root) -> dict:
    capability = _child(root, "Capability")
    formats = [_text(f) for f in _children(_find(capability, "Request", "GetMap"), "Format")]
    endpoint = service_base(final_url) if final_url else service_base(
        _href(_find(capability, "Request", "GetMap", "DCPType", "HTTP", "Get", "OnlineResource"))
        or document_endpoint(root))
    named: list[tuple[str, str, list]] = []
    service_crs: list[str] = []

    def walk(layer, inherited: list) -> None:
        own = []
        for element in layer:
            if _local(element.tag) in ("CRS", "SRS"):
                own += [epsg_of(value) or value for value in (element.text or "").split()]
        crs = _unique(inherited + own)
        if not service_crs:
            service_crs.extend(crs)
        name = _text(_child(layer, "Name"))
        if name:
            named.append((name, _text(_child(layer, "Title")), crs))
        for sub in _children(layer, "Layer"):
            walk(sub, crs)

    for top in _children(capability, "Layer"):
        walk(top, [])
    entries = []
    for name, title, crs in named[:MAX_LAYERS]:
        args = {"source": endpoint, "kind": "wms", "layer": name}
        chosen = _pick_crs(crs)
        if chosen:
            args["crs"] = chosen
        entries.append({"name": name, **({"title": _short(title)} if title else {}),
                        "add": {"tool": "add_data", "args": args}})
    out = _listing("wms_capabilities", "WMS", final_url, endpoint, root, entries, [(n, t) for n, t, _ in named],
                   crs=service_crs, formats=formats,
                   message=("WMS capabilities read. Each layer carries the add_data call that loads it, with the "
                            "CRS chosen from the ones it is served in (EPSG:3857 when offered; crs lists the "
                            "others). " + _PICTURES_NOT_VALUES))


    service = _child(root, "Service")
    for key, tag in (("max_width", "MaxWidth"), ("max_height", "MaxHeight")):
        try:
            size = int(_text(_child(service, tag))) if service is not None else 0
        except ValueError:
            size = 0
        if size > 0:
            out[key] = size
    return out




def _wfs(final_url: str, root) -> dict:
    endpoint = _endpoint(final_url, root)
    types = _children(_child(root, "FeatureTypeList"), "FeatureType")
    names = [(_text(_child(t, "Name")), _text(_child(t, "Title"))) for t in types]
    entries = []
    for element in types[:MAX_LAYERS]:
        name = _text(_child(element, "Name"))
        if not name:
            continue
        title = _text(_child(element, "Title"))
        default = next((_text(_child(element, tag)) for tag in ("DefaultCRS", "DefaultSRS", "SRS")
                        if _child(element, tag) is not None), "")
        entries.append({"name": name, **({"title": _short(title)} if title else {}),
                        **({"crs": epsg_of(default) or default} if default else {}),
                        "add": {"tool": "add_data", "args": {"source": endpoint, "kind": "wfs", "layer": name}}})
    page = _wfs_page_size(root)
    message = ("WFS capabilities read. Each feature type carries the add_data call that loads it as "
               "vector features. It loads 1,000 features at most, and a type holding more is fetched for "
               "the map view only: zoom to the area first.")
    if page:
        message += (f" The service answers at most {page:,} features per request (CountDefault) and says "
                    "nothing when it stops there.")
    out = _listing("wfs_capabilities", "WFS", final_url, endpoint, root, entries, [n for n in names if n[0]],
                   message=message)
    if page:
        out["page_size"] = page
    return out


def _wfs_page_size(root) -> int:
    """The CountDefault a WFS 2.0 publishes, or 0."""




    for element in root.iter():
        if _local(element.tag) == "Constraint" and element.get("name") == "CountDefault":
            value = _find(element, "DefaultValue")
            try:
                return max(0, int(float(_text(value))))
            except (TypeError, ValueError):
                return 0
    return 0




def _envelope(element) -> list | None:
    """[west, south, east, north] of a lonLatEnvelope or a WGS84BoundingBox, rounded, else None."""
    if element is None:
        return None
    corners = [_text(c) for c in element if _local(c.tag) in ("pos", "LowerCorner", "UpperCorner")]
    try:
        (west, south), (east, north) = [[float(v) for v in corner.split()[:2]] for corner in corners[:2]]
    except (ValueError, TypeError):
        return None
    return [round(west, 2), round(south, 2), round(east, 2), round(north, 2)]


def _wcs_entry(endpoint: str, coverage: str, title: str, bbox: list | None, crs: list) -> dict:
    entry = {"name": coverage, **({"title": _short(title)} if title else {})}
    if crs:
        entry["crs"] = crs[:4]
    if bbox:
        entry["wgs84_bbox"] = bbox
    entry["add"] = {"tool": "add_data", "args": {"source": endpoint, "kind": "wcs", "layer": coverage}}
    return entry


_WCS_MESSAGE = ("WCS capabilities read: coverages of real values (heights, temperatures), not pictures. Each "
                "carries the add_data call that streams it. Add bbox [west, south, east, north] in EPSG:4326 "
                "to the same call to download that box as a local GeoTIFF instead, which is what slope, "
                "contours, hillshade and zonal statistics should run on.")


def _wcs10(final_url: str, root) -> dict:
    endpoint = service_base(final_url) if final_url else service_base(
        _href(_find(root, "Capability", "Request", "GetCoverage", "DCPType", "HTTP", "Get", "OnlineResource"))
        or document_endpoint(root))
    briefs = _children(_child(root, "ContentMetadata"), "CoverageOfferingBrief")
    entries = [_wcs_entry(endpoint, _text(_child(b, "name")), _text(_child(b, "label")),
                          _envelope(_child(b, "lonLatEnvelope")), [])
               for b in briefs[:MAX_LAYERS] if _text(_child(b, "name"))]
    return _listing("wcs_capabilities", "WCS", final_url, endpoint, root, entries,
                    [(_text(_child(b, "name")), _text(_child(b, "label"))) for b in briefs], message=_WCS_MESSAGE,
                    local_copy=_local_copy(entries))


def _wcs_ows(final_url: str, root) -> dict:
    """WCS 1.1 and 2.0, which share the OWS common layout."""
    endpoint = _endpoint(final_url, root)
    metadata = _child(root, "ServiceMetadata")
    formats = [_text(f) for f in metadata.iter() if _local(f.tag) == "formatSupported"] if metadata is not None else []
    service_crs = _unique(epsg_of(_text(c)) for c in root.iter() if _local(c.tag) == "crsSupported")
    summaries = [s for s in root.iter() if _local(s.tag) == "CoverageSummary"]
    names, entries = [], []
    for summary in summaries:
        coverage = _text(_child(summary, "CoverageId")) or _text(_child(summary, "Identifier"))
        if not coverage:
            continue
        names.append(coverage)
        if len(entries) >= MAX_LAYERS:
            continue
        crs = _unique(epsg_of(_text(c)) for c in summary if _local(c.tag) == "SupportedCRS")
        formats += [_text(f) for f in summary if _local(f.tag) == "SupportedFormat"]
        entries.append(_wcs_entry(endpoint, coverage, _text(_child(summary, "Title")),
                                  _envelope(_child(summary, "WGS84BoundingBox")), crs))
    return _listing("wcs_capabilities", "WCS", final_url, endpoint, root, entries, names,
                    crs=service_crs, formats=_unique(formats), message=_WCS_MESSAGE,
                    local_copy=_local_copy(entries))


def wcs10_description(body: bytes) -> dict:
    """What a WCS 1.0.0 DescribeCoverage says the download needs: formats, native CRS, cell size."""






    import xml.etree.ElementTree as ET  # nosec B405 - the caller's body is a service answer; entities are refused

    text = decode_xml(body)
    if "<!ENTITY" in text.upper():
        return {"exception": "the answer declares XML entities"}
    try:
        root = ET.fromstring(strip_doctype(text))  # nosec B314 - entities refused above
    except ET.ParseError:
        return {"exception": _short(text, 240)}
    if _local(root.tag).lower() in ("exceptionreport", "serviceexceptionreport"):
        return {"exception": exception_report("", root)["exception"]}
    formats = [_text(f) for f in root.iter() if _local(f.tag) == "formats"]
    native = [_text(c) for c in root.iter() if _local(c.tag) == "nativeCRSs"]
    offered = [_text(c) for c in root.iter() if _local(c.tag) in ("requestResponseCRSs", "responseCRSs")]
    envelopes = [e.get("srsName", "") for e in root.iter() if _local(e.tag) == "Envelope"]
    grid_crs = next((epsg_of(name) for name in envelopes if epsg_of(name) not in ("", "EPSG:4326")), "")
    crs = epsg_of((native or [""])[0].split()[0] if native and native[0] else "") or grid_crs or epsg_of(
        (offered or [""])[0])
    resolution = 0.0
    for vector in (v for v in root.iter() if _local(v.tag) == "offsetVector"):
        try:
            resolution = max(abs(float(value)) for value in _text(vector).split())
        except ValueError:
            continue
        if resolution:
            break
    return {"formats": formats, "crs": crs, "resolution": resolution,
            "crs_offered": _unique(epsg_of(c) for c in offered)}


def pick_tiff_format(formats: list) -> str:
    """The GeoTIFF among a coverage's formats, a float one first; "" when it offers none."""
    tiffs = [f for f in formats if "tif" in f.lower() and "rgb" not in f.lower()]
    return next((f for f in tiffs if "float" in f.lower()), tiffs[0] if tiffs else "")


def _local_copy(entries: list) -> dict | None:
    if not entries:
        return None
    args = dict(entries[0]["add"]["args"], bbox="{BBOX_4326}")
    return {"tool": "add_data", "args": args}




def _service_title(root) -> str:
    for path in (("Service", "Title"), ("ServiceIdentification", "Title"), ("Service", "label")):
        element = _find(root, *path)
        if element is not None and _text(element):
            return _short(_text(element))
    return ""


def _listing(family: str, service: str, final_url: str, endpoint: str, root, entries: list, names: list,
             message: str, crs: list | None = None, formats: list | None = None,
             local_copy: dict | None = None) -> dict:
    out: dict = {"source_family": family, "service": service}
    version = root.get("version") if root is not None else ""
    if version:
        out["version"] = version
    title = _service_title(root) if root is not None else ""
    if title:
        out["title"] = title
    if final_url:
        out["final_url"] = final_url
    out["service_url"] = endpoint
    if crs:
        out["crs"] = crs[:_CRS_SHOWN]
        if len(crs) > _CRS_SHOWN:
            out["crs_count"] = len(crs)
    if formats:
        out["formats"] = _unique(formats)[:_FORMATS_SHOWN]
    out["layer_count"] = len(names)
    out["layers"] = entries
    listed = {entry["name"] for entry in entries}
    others = []
    for item in names:
        name, title = item if isinstance(item, tuple) else (item, "")
        if name and name not in listed:
            title = _short(title, _OTHER_TITLE_CHARS)
            others.append(f"{name}: {title}" if title and title != name else name)
    if others:
        out["other_layers"] = others[:MAX_OTHER_NAMES]
        message += (f" {len(names)} layers: the first {len(entries)} carry their call, the next "
                    f"{min(len(others), MAX_OTHER_NAMES)} are in other_layers as name: title, and load with the "
                    "same call with that name as layer.")
    if local_copy:
        out["local_copy"] = local_copy
    if len(entries) == 1:
        out["import_method"] = entries[0]["add"]["tool"]
        out["import_arguments"] = entries[0]["add"]["args"]
    if not endpoint:
        message += (" The document does not name its own address: ask the user for the service URL before "
                    "loading anything from it.")
    out["message"] = message
    return out


def names_listing(family: str, final_url: str, names: list) -> dict:
    """The answer for a document too large to parse into a tree: its names, each with its call."""
    endpoint = service_base(final_url)
    service, kind = ("WFS", "wfs") if family == "wfs_capabilities" else ("WMS", "wms")
    entries = [{"name": name, "add": {"tool": "add_data", "args": {"source": endpoint, "kind": kind, "layer": name}}}
               for name in names[:MAX_LAYERS]]
    out = _listing(family, service, final_url, endpoint, None, entries, names,
                   message=(f"{service} capabilities read in one pass: the document is too large for a tree, so "
                            "titles and CRS are not listed. Each name carries the add_data call that loads it."))
    out["truncated"] = True
    return out


def exception_report(final_url: str, root) -> dict:
    text = " ".join(_text(element) for element in root.iter()
                    if _local(element.tag) in ("ExceptionText", "ServiceException"))
    return {"source_family": "ogc_exception", "final_url": final_url,
            "exception": _short(text or "no text", 240),
            "message": "The service answered with an exception instead of describing itself."}


def xml_capabilities(final_url: str, root, text: str) -> dict | None:
    """The listing for an OGC capabilities document, an exception answer, or None."""
    name = _local(root.tag).lower()
    namespace = _namespace(root.tag)
    if name in ("wms_capabilities", "wmt_ms_capabilities"):
        return _wms(final_url, root)
    if name == "wfs_capabilities":
        return _wfs(final_url, root)
    if name == "wcs_capabilities":
        return _wcs10(final_url, root)
    if name == "capabilities":
        if "wmts" in namespace:
            return _wmts(final_url, root, text)
        if "wcs" in namespace:
            return _wcs_ows(final_url, root)
        if "wfs" in namespace:
            return _wfs(final_url, root)
        if _find(root, "Contents") is not None and "<TileMatrixSet" in text:
            return _wmts(final_url, root, text)
    if name in ("exceptionreport", "serviceexceptionreport"):
        return exception_report(final_url, root)
    return None


_XML_ENCODING_RE = re.compile(rb"""^\s*<\?xml[^>]*encoding=["']([A-Za-z0-9_.-]+)["']""")
_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", re.DOTALL | re.IGNORECASE)


def decode_xml(body: bytes) -> str:
    """The document as text in the encoding it declares: IGN's WCS 1.0 answer is ISO-8859-1."""
    found = _XML_ENCODING_RE.match(body[:200] or b"")
    if found:
        try:
            return body.decode(found.group(1).decode("ascii"), "replace")
        except LookupError:
            pass
    return body.decode("utf-8", "ignore")


def strip_doctype(text: str) -> str:
    """The document without its DOCTYPE, which a WMS 1.1.1 answer carries by schema."""






    return _DOCTYPE_RE.sub("", text, count=1)




_ARCGIS_SERVICE_RE = re.compile(r"^(?P<url>.*?/(?P<type>MapServer|FeatureServer|ImageServer))(?:/(?P<layer>\d+))?/?$",
                                re.IGNORECASE)
_REL_TILESETS = ("tilesets-vector", "tilesets-map")


def _arcgis_crs(payload: dict) -> str:
    reference = payload.get("spatialReference") or (payload.get("extent") or {}).get("spatialReference") or {}
    code = reference.get("latestWkid") or reference.get("wkid") if isinstance(reference, dict) else None
    return epsg_of(str(code)) if code else ""


def _arcgis(final_url: str, payload: dict) -> dict | None:
    base = service_base(final_url)
    path = urllib.parse.urlsplit(base).path
    if "currentVersion" in payload and ("services" in payload or "folders" in payload) and "layers" not in payload:
        access = urllib.parse.urlsplit(base).query
        root = re.split(r"(?i)(?<=/rest/services)", base.split("?", 1)[0], maxsplit=1)[0].rstrip("/")
        services, skipped = [], 0
        for service in payload.get("services") or []:
            if not isinstance(service, dict):
                continue
            kind, name = str(service.get("type") or ""), str(service.get("name") or "")

            url = f"{root}/{name}/{kind}" + (f"?{access}" if access else "")
            if kind in ("MapServer", "ImageServer"):
                call = {"tool": "add_data", "args": {"source": url, "kind": "raster"}}
            elif kind == "FeatureServer":
                call = {"tool": "inspect_data_source", "args": {"url": url}}
            else:
                skipped += 1
                continue
            if len(services) < MAX_LAYERS:
                services.append({"name": name, "type": kind, "add": call})
        folders = [folder for folder in payload.get("folders") or [] if isinstance(folder, str)]
        out = {"source_family": "arcgis_services", "service": "ArcGIS REST", "final_url": final_url,
               "service_url": root, "layers": services, "layer_count": len(payload.get("services") or [])}
        if folders:
            out["folders"] = folders[:MAX_OTHER_NAMES]
            out["folder_call"] = {"tool": "inspect_data_source",
                                  "args": {"url": f"{root}/{{FOLDER}}" + (f"?{access}" if access else "")}}
        out["message"] = ("ArcGIS REST directory. A MapServer or ImageServer loads as an image with its add call; "
                          "inspect a FeatureServer to list its layers, and a folder to list its services."
                          + (f" {skipped} services of other types (geocoding, geoprocessing) are left out."
                             if skipped else ""))
        return out
    match = _ARCGIS_SERVICE_RE.match(path)
    if not match:
        return None
    parts = urllib.parse.urlsplit(base)
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, match.group("url"), parts.query, ""))
    kind = match.group("type").lower()
    crs = _arcgis_crs(payload)
    if match.group("layer") is not None:
        layer_url = with_path(url, f"/{match.group('layer')}")
        vector = str(payload.get("type") or "") in ("Feature Layer", "Table") or bool(payload.get("geometryType"))
        out = {"source_family": "arcgis_layer", "service": "ArcGIS REST", "final_url": final_url,
               "service_url": layer_url, "name": _short(payload.get("name") or ""),
               "type": payload.get("type"), **({"crs": crs} if crs else {})}
        if payload.get("geometryType"):
            out["geometry_type"] = str(payload["geometryType"]).replace("esriGeometry", "").lower()


        if isinstance(payload.get("maxRecordCount"), int) and payload["maxRecordCount"] > 0:
            out["page_size"] = payload["maxRecordCount"]
        if vector:
            out["import_method"] = "add_data"
            out["import_arguments"] = {"source": layer_url, "kind": "vector"}
            out["message"] = "An ArcGIS feature layer: it loads as vector features with the import call."
        else:
            out["import_method"] = "add_data"
            out["import_arguments"] = {"source": url, "kind": "raster"}
            out["message"] = "Not a feature layer: the service it belongs to loads as an image."
        return out
    if kind == "imageserver":
        out = {"source_family": "arcgis_imageserver", "service": "ArcGIS REST", "final_url": final_url,
               "service_url": url, "name": _short(payload.get("name") or ""), **({"crs": crs} if crs else {}),
               "band_count": payload.get("bandCount"), "pixel_type": payload.get("pixelType"),
               "import_method": "add_data", "import_arguments": {"source": url, "kind": "raster"},
               "message": "An ArcGIS image service: it loads as a raster layer with the import call."}
        if payload.get("pixelSizeX"):
            out["pixel_size"] = payload.get("pixelSizeX")
        return out
    queryable = kind == "featureserver" or "query" in str(payload.get("capabilities") or "").lower()
    entries, names = [], []
    for layer in list(payload.get("layers") or []) + list(payload.get("tables") or []):
        if not isinstance(layer, dict) or layer.get("subLayerIds"):
            continue
        if str(layer.get("type") or "Feature Layer") not in ("Feature Layer", "Table"):
            continue
        names.append(str(layer.get("name")))
        if len(entries) < MAX_LAYERS:
            entry = {"name": _short(layer.get("name") or ""), "id": layer.get("id")}
            if layer.get("geometryType"):
                entry["geometry_type"] = str(layer["geometryType"]).replace("esriGeometry", "").lower()
            if queryable:
                entry["add"] = {"tool": "add_data", "args": {"source": with_path(url, f"/{layer.get('id')}"),
                                                             "kind": "vector"}}
            entries.append(entry)
    family = "arcgis_featureserver" if kind == "featureserver" else "arcgis_mapserver"
    out = {"source_family": family, "service": "ArcGIS REST", "final_url": final_url, "service_url": url,
           **({"title": _short(payload.get("mapName") or payload.get("serviceDescription") or "")}
              if payload.get("mapName") else {}),
           **({"crs": crs} if crs else {}), "layer_count": len(names), "layers": entries}
    if kind == "mapserver":
        out["add_whole_service"] = {"tool": "add_data", "args": {"source": url, "kind": "raster"}}
    out["message"] = ("ArcGIS REST service. " + (
        "add_whole_service draws every layer as one image; each layer's add call loads its features."
        if kind == "mapserver" else "Each layer's add call loads its features.")
        + ("" if queryable else " This service does not allow queries, so its layers draw as an image only."))
    return out


def _links(payload: dict) -> list[dict]:
    return [link for link in payload.get("links") or [] if isinstance(link, dict) and link.get("href")]


def _rel(link: dict) -> str:
    return str(link.get("rel") or "").rsplit("/", 1)[-1]


def _tile_template(href: str, matrix_set: str) -> str:
    return (href.replace("{tileMatrixSetId}", matrix_set).replace("{tileMatrix}", "{z}")
            .replace("{tileRow}", "{y}").replace("{tileCol}", "{x}"))


def _tilesets(final_url: str, payload: dict) -> dict:
    parent_items = [link["href"] for link in _links(payload) if _rel(link) == "item"]
    sets = payload.get("tilesets") if isinstance(payload.get("tilesets"), list) else [payload]
    entries = []
    for tileset in sets:
        if not isinstance(tileset, dict):
            continue
        matrix_set = str(tileset.get("tileMatrixSetId") or tileset.get("tileMatrixSetURI") or "").rstrip("/")
        matrix_set = matrix_set.rsplit("/", 1)[-1]
        data_type = str(tileset.get("dataType") or "")
        entry = {"name": matrix_set or "tileset",
                 **({"title": _short(tileset["title"])} if tileset.get("title") else {}),
                 "data_type": data_type}
        items = [link["href"] for link in _links(tileset) if _rel(link) == "item"] or parent_items
        own = next((link["href"] for link in _links(tileset) if _rel(link) == "self"), "")
        if matrix_set == "WebMercatorQuad" and items:
            entry["add"] = {"tool": "add_data", "args": {
                "source": _tile_template(items[0], matrix_set),
                "kind": "vectortile" if data_type == "vector" else "xyz"}}
        elif matrix_set == "WebMercatorQuad" and own:
            entry["add"] = {"tool": "inspect_data_source", "args": {"url": own}}
        else:
            entry["note"] = "Not in Web Mercator: QGIS reads only WebMercatorQuad tiles as XYZ."
        entries.append(entry)
    return {"source_family": "ogc_api_tilesets", "service": "OGC API - Tiles", "final_url": final_url,
            "layer_count": len(entries), "layers": entries[:MAX_LAYERS],
            "message": "OGC API tilesets. The WebMercatorQuad one loads as a tile layer with its add call."}


def _collections(final_url: str, payload: dict) -> dict:
    base = service_base(final_url)
    access = urllib.parse.urlsplit(base).query
    path_base = base.split("?", 1)[0].rstrip("/")
    root = path_base[: -len("/collections")] if path_base.endswith("/collections") else path_base
    root = root + (f"?{access}" if access else "")
    entries, names, kinds = [], [], set()
    for collection in payload.get("collections") or []:
        if not isinstance(collection, dict):
            continue
        identifier = str(collection.get("id") or collection.get("name") or "")
        if not identifier:
            continue
        names.append((identifier, str(collection.get("title") or "")))
        links = _links(collection)
        rels = {_rel(link) for link in links}
        item_type = str(collection.get("itemType") or "").lower()
        items_url = with_path(root, f"/collections/{urllib.parse.quote(identifier)}/items")
        tilesets = next((link["href"] for link in links if _rel(link) in _REL_TILESETS), "")
        if "items" in rels and item_type == "record":
            kind, call = "records", {"tool": "fetch_json", "args": {"url": items_url}}
        elif "items" in rels:
            kind, call = "features", {"tool": "add_data", "args": {"source": items_url, "kind": "vector"}}
        elif tilesets:
            kind, call = "tiles", {"tool": "inspect_data_source", "args": {"url": service_base(tilesets)}}
        elif "coverage" in rels:
            kind, call = "coverage", None
        elif "map" in rels:
            kind, call = "map", None
        else:
            kind, call = "other", None
        kinds.add(kind)
        if len(entries) < MAX_LAYERS:
            entry = {"name": identifier, **({"title": _short(collection["title"])} if collection.get("title") else {}),
                     "kind": kind}
            if tilesets and kind != "tiles":
                entry["tiles"] = {"tool": "inspect_data_source", "args": {"url": service_base(tilesets)}}
            if call:
                entry["add"] = call
            entries.append(entry)
    out = {"source_family": "ogc_api_collections", "service": "OGC API", "final_url": final_url,
           "service_url": root, "layer_count": len(names), "layers": entries}
    others = [f"{name}: {_short(title, _OTHER_TITLE_CHARS)}" if title else name
              for name, title in names[len(entries):]]
    if others:
        out["other_layers"] = others[:MAX_OTHER_NAMES]
    out["message"] = ("OGC API collections. A features collection loads with its add call, paged by the OGC API "
                      "driver rather than read as one page; records are a catalogue, read with fetch_json; tiles "
                      "are listed by inspecting the tiles URL."
                      + (" Coverage and map collections have no loader here." if kinds & {"coverage", "map"}
                         else ""))
    return out


def is_stac_collections(payload: dict) -> bool:
    if "stac_version" in payload:
        return True
    return any(isinstance(c, dict) and ("stac_version" in c or c.get("type") == "Collection")
               for c in payload.get("collections") or [])


def json_service(final_url: str, payload) -> dict | None:
    """The listing for an ArcGIS REST or OGC API answer, or None for any other JSON."""




    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("error"), dict) and "currentVersion" not in payload:
        error = payload["error"]
        if "code" in error and "message" in error and _ARCGIS_RE.search(urllib.parse.urlsplit(final_url).path):
            return {"source_family": "arcgis_error", "final_url": final_url,
                    "status": error.get("code"), "exception": _short(error.get("message") or "", 240),
                    "message": "The ArcGIS service refused: a token-protected service is not open data."}
    arcgis = _arcgis(final_url, payload)
    if arcgis is not None:
        return arcgis
    if isinstance(payload.get("tilesets"), list) or (
            (payload.get("tileMatrixSetURI") or payload.get("tileMatrixSetId")) and payload.get("links")):
        return _tilesets(final_url, payload)
    if isinstance(payload.get("collections"), list) and not is_stac_collections(payload):
        return _collections(final_url, payload)
    if "collections" not in payload and "stac_version" not in payload:
        data = next((link["href"] for link in _links(payload) if _rel(link) == "data"), "")
        if data:
            return {"follow": _with_query(service_base(data), {"f": "json"})}
    return None


def pasted_json(text: str):
    """The JSON a user pasted, or None."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None
