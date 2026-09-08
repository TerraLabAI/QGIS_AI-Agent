# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Third harvest batch, remote sources: ArcGIS REST services and the assets of a STAC item."""









from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsDataSourceUri, QgsProject, QgsRasterLayer, QgsUnitTypes, QgsVectorLayer, QgsWkbTypes

from ..core import net
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import stac_tools as _stac
from . import volume_guard
from .data_tools import _run_on_main_thread
from .harvest_view import _extent_dict, _feature_count

_ARCGIS_URL_RE = re.compile(
    r"^(?P<root>.*?/(?P<service>FeatureServer|MapServer|ImageServer))(?:/(?P<layer>\d+))?/?$",
    re.IGNORECASE,
)
_ARCGIS_TIMEOUT = 15




_USER_AGENT = net.user_agent()
_RASTER_MEDIA = ("geotiff", "image/tiff", "image/jp2", "jpeg2000", "x-hdf", "netcdf", "zarr")
_PREVIEW_MEDIA = ("image/png", "image/jpeg", "image/jpg", "image/webp")


def register_harvest_remote_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="add_arcgis_rest_layer",
        input_schema={
            "type": "object",
            "properties": {
                "confirm_large": {"type": "boolean"},
                "url": {
                    "type": "string",
                },
                "kind": {
                    "type": "string",
                    "enum": ["auto", "feature", "map"],
                },
                "name": {
                    "type": "string",
                },
                "token": {"type": "string"},
                "crs": {
                    "type": "string",
                },
            },
            "required": ["url"],
        },
        handler=_add_arcgis_rest_layer,
        background=True,
    ))

    registry.register(Tool(
        name="get_stac_item_assets",
        input_schema={
            "type": "object",
            "properties": {
                "item_url": {
                    "type": "string",
                },
                "stac_url": {
                    "type": "string",
                },
                "collection": {"type": "string"},
                "item_id": {"type": "string"},
            },
        },
        handler=_get_stac_item_assets,
        background=True,
    ))





def _describe_added(layer, kind: str, url: str) -> dict:
    """The shape the other add_* tools return: ids, counts, crs, extent with its unit."""
    out = {
        "layer_id": layer.id(),
        "layer_name": layer.name(),
        "kind": kind,
        "provider": layer.providerType(),
        "url": url,
        "crs": layer.crs().authid(),
        "extent": _extent_dict(layer.extent()),
    }
    try:
        out["units"] = QgsUnitTypes.toString(layer.crs().mapUnits())
    except Exception:
        out["units"] = "unknown"
    if isinstance(layer, QgsVectorLayer):
        out["feature_count"] = _feature_count(layer)
        try:
            out["geometry_type"] = QgsWkbTypes.geometryDisplayString(layer.geometryType())
        except Exception:  # nosec B110 - layer error detail is optional
            out["geometry_type"] = str(layer.geometryType())
        out["fields"] = [f.name() for f in layer.fields()][:40]
    elif isinstance(layer, QgsRasterLayer):
        out["band_count"] = layer.bandCount()
    return out





def _arcgis_parse(url: str):
    """(root, service, layer_id, clean_url) or None when the URL is not a REST service URL."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    match = _ARCGIS_URL_RE.match(parts.path)
    if not match:
        return None
    root = f"{parts.scheme}://{parts.netloc}{match.group('root')}"
    layer_id = match.group("layer")
    clean = f"{root}/{layer_id}" if layer_id is not None else root
    return root, match.group("service").lower(), layer_id, clean


_ARCGIS_MAX_BYTES = 4 * 1024 * 1024
_TOTAL_TIMEOUT_FACTOR = 3
_CACHE_ARCGIS_S = 300


def _arcgis_describe(url: str, token: str | None):
    """The service or layer JSON (an "error" key when the service refuses), or None offline."""
    query = {"f": "json"}
    if token:
        query["token"] = token
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return None
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(query)}",
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    try:


        raw = net.fetch(request, timeout=_ARCGIS_TIMEOUT, max_bytes=_ARCGIS_MAX_BYTES,
                        total_timeout=_ARCGIS_TIMEOUT * _TOTAL_TIMEOUT_FACTOR,
                        cache_ttl=_CACHE_ARCGIS_S).body
        payload = json.loads(raw)
    except net.FetchCancelled:





        raise
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _arcgis_wkid(info: dict) -> str | None:
    for key in ("extent", "fullExtent", "initialExtent"):
        extent = info.get(key)
        if isinstance(extent, dict):
            reference = extent.get("spatialReference") or {}
            wkid = reference.get("latestWkid") or reference.get("wkid")
            if wkid:
                return f"EPSG:{wkid}"
    reference = info.get("spatialReference") or {}
    wkid = reference.get("latestWkid") or reference.get("wkid")
    return f"EPSG:{wkid}" if wkid else None


def _arcgis_name(args: dict, info: dict | None, root: str, layer_id: str | None) -> str:
    """The caller's name, else the service's, else the service folder in the URL."""
    name = str(args.get("name") or "").strip()
    if name:
        return name
    if info:
        name = str(info.get("name") or "").strip()
        if not name:
            map_name = str(info.get("mapName") or "").strip()
            if map_name and map_name.lower() != "layers":
                name = map_name
        if name:
            return name
    segments = [s for s in root.rstrip("/").split("/") if s]
    service_name = segments[-2] if len(segments) >= 2 else "ArcGIS"
    if layer_id is not None:
        return f"{service_name} {layer_id}"
    return f"{service_name} ({segments[-1]})" if segments else "ArcGIS layer"


def _arcgis_summary(info: dict) -> dict:
    summary = {}
    labels = (
        ("name", "name"), ("type", "type"), ("geometryType", "geometry"),
        ("maxRecordCount", "max_record_count"), ("description", "description"),
    )
    for key, label in labels:
        value = info.get(key)
        if value:
            summary[label] = str(value)[:300] if label == "description" else value
    if isinstance(info.get("fields"), list):
        summary["field_count"] = len(info["fields"])
    if isinstance(info.get("layers"), list):
        summary["layers"] = [
            {"id": entry.get("id"), "name": entry.get("name")}
            for entry in info["layers"][:30] if isinstance(entry, dict)
        ]
    wkid = _arcgis_wkid(info)
    if wkid:
        summary["native_crs"] = wkid
    return summary


def _add_arcgis_rest_layer(args: dict) -> dict:
    parsed = _arcgis_parse(str(args.get("url") or ""))
    if parsed is None:
        return tool_error(
            "The URL is not an ArcGIS REST service URL.",
            "INVALID_ARGS",
            "Pass a URL ending in /FeatureServer/<id>, /MapServer/<id>, /MapServer or /ImageServer.",
        )
    root, service, layer_id, url = parsed
    token = str(args.get("token") or "").strip() or None
    kind = str(args.get("kind") or "auto").lower()
    if kind == "auto":
        kind = "feature" if (service == "featureserver" or layer_id is not None) else "map"
    if kind == "feature" and layer_id is None:
        info = _arcgis_describe(root, token)
        layers = (info or {}).get("layers") or []
        listing = ", ".join(
            f"{entry.get('id')}: {entry.get('name')}" for entry in layers[:15] if isinstance(entry, dict)
        )
        return tool_error(
            "A feature layer needs the layer id at the end of the URL.",
            "INVALID_ARGS",
            f"Append /<id> to the URL. Layers on this service: {listing}" if listing
            else "Append /<id> to the URL; open <url>?f=json to see the layer ids.",
        )

    info = _arcgis_describe(url, token)
    if info and isinstance(info.get("error"), dict):
        err = info["error"]
        code = err.get("code")
        detail = "; ".join(str(d) for d in (err.get("details") or []) if d)
        message = f"The service answered {code}: {err.get('message')}" + (f" ({detail})" if detail else "")
        suggestion = "Pass a valid token." if code in (498, 499) else "Check the URL in a browser with ?f=json."
        return tool_error(message, "ARCGIS_REFUSED", suggestion)

    name = _arcgis_name(args, info, root, layer_id)
    crs = str(args.get("crs") or "").strip()



    def _create():
        uri = QgsDataSourceUri()
        if kind == "feature":
            uri.setParam("url", url)
            uri.setParam("crs", crs or _arcgis_wkid(info or {}) or "EPSG:4326")
            if token:
                uri.setParam("token", token)
            layer = QgsVectorLayer(uri.uri(False), name, "arcgisfeatureserver")
        else:
            uri.setParam("url", root)
            if layer_id is not None:
                uri.setParam("layer", layer_id)
            uri.setParam("format", "PNG32")
            uri.setParam("crs", crs or "EPSG:3857")
            if token:
                uri.setParam("token", token)
            layer = QgsRasterLayer(uri.uri(False), name, "arcgismapserver")

        if not layer.isValid():
            reason = ""
            try:
                reason = layer.error().summary() or layer.dataProvider().error().summary()
            except Exception:  # nosec B110 - layer error detail is optional
                pass
            return tool_error(
                f"QGIS could not open the {kind} layer at {url}." + (f" Provider: {reason}" if reason else ""),
                "INVALID_ARGS",
                "Check the URL with ?f=json, pass token for a secured service, or switch kind "
                "(feature needs a layer URL with /<id>; map takes the MapServer service URL).",
            )
        refused = volume_guard.too_many_features(layer, args, "The ArcGIS service answer")
        if refused:
            return refused
        QgsProject.instance().addMapLayer(layer)
        return _describe_added(layer, kind, url)

    out = _run_on_main_thread(_create, timeout=120)
    if out.get("_error"):
        return out
    out["service"] = service
    if layer_id is not None:
        out["service_layer_id"] = int(layer_id)
    if info:
        out["service_info"] = _arcgis_summary(info)
    return out





def _asset_row(key: str, asset: dict) -> dict:
    media = str(asset.get("type") or "")
    lower = media.lower()
    href = str(asset.get("href") or "")
    row = {
        "key": key,
        "href": href or None,
        "type": media or None,
        "roles": [str(r) for r in (asset.get("roles") or [])],
        "title": asset.get("title"),
        "is_cog": "cloud-optimized" in lower,
    }
    bands = asset.get("eo:bands") or asset.get("raster:bands")
    if isinstance(bands, list) and bands:
        names = []
        for band in bands:
            if isinstance(band, dict):
                names.append(band.get("common_name") or band.get("name") or band.get("description"))
        names = [n for n in names if n]
        if names:
            row["bands"] = names[:20]
    extras = (
        ("gsd", "gsd"), ("file:size", "size_bytes"), ("proj:shape", "shape"),
        ("proj:epsg", "epsg"), ("proj:code", "proj_code"),
    )
    for key_in, key_out in extras:
        if asset.get(key_in) is not None:
            row[key_out] = asset[key_in]
    if "blob.core.windows.net" in href and "sig=" not in href.lower():
        row["needs_signing"] = True
    return row


def _asset_rank(row: dict):
    lower = str(row.get("type") or "").lower()
    roles = set(row.get("roles") or [])
    if row.get("is_cog"):
        rank = 0
    elif any(hint in lower for hint in _RASTER_MEDIA):
        rank = 1
    elif any(hint in lower for hint in _PREVIEW_MEDIA) or roles & {"thumbnail", "overview"}:
        rank = 2
    elif "json" in lower or "xml" in lower or "text" in lower or roles & {"metadata"}:
        rank = 3
    else:
        rank = 4
    return (rank, 0 if "data" in roles else 1, str(row.get("key")))


def _get_stac_item_assets(args: dict) -> dict:
    item_url = str(args.get("item_url") or "").strip()
    if not item_url:
        collection = str(args.get("collection") or "").strip()
        item_id = str(args.get("item_id") or "").strip()
        if not (collection and item_id):
            return tool_error(
                "Provide item_url, or collection + item_id (plus stac_url when the catalog is not "
                "Planetary Computer).",
                "INVALID_ARGS",
                "search_stac_items returns item ids and URLs; pass one of them.",
            )
        item_url = (
            f"{_stac._stac_root(args)}/collections/{urllib.parse.quote(collection)}"
            f"/items/{urllib.parse.quote(item_id)}"
        )
    try:
        item = _stac._stac_get(item_url)
    except net.FetchCancelled:


        return tool_error("The run was stopped.", "CANCELLED", "Wait for the next user message.")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return tool_error(
            f"Failed to fetch the STAC item: {e}",
            "STAC_FETCH_FAILED",
            "Check item_url (or stac_url, collection and item_id); search_stac_items gives working ids.",
        )
    if not isinstance(item, dict) or not isinstance(item.get("assets"), dict):
        return tool_error(
            "The URL did not return a STAC item with assets.",
            "INVALID_ARGS",
            "item_url must be an item (.../collections/<collection>/items/<id>), not a collection or a search.",
        )
    rows = [_asset_row(key, asset) for key, asset in item["assets"].items() if isinstance(asset, dict)]
    rows.sort(key=_asset_rank)
    properties = item.get("properties") or {}
    out = {
        "item_url": item_url,
        "item_id": item.get("id"),
        "collection": item.get("collection"),
        "datetime": properties.get("datetime"),
        "bbox": item.get("bbox"),
        "cloud_cover": properties.get("eo:cloud_cover"),
        "platform": properties.get("platform"),
        "gsd": properties.get("gsd"),
        "count": len(rows),
        "cog_keys": [r["key"] for r in rows if r.get("is_cog")],
        "assets": rows,
        "next": "add_data(source=item_url, kind='stac', layer=<asset key>) loads one asset; "
                "add_cog_layer(url=<href>) loads any COG href.",
    }
    if any(r.get("needs_signing") for r in rows):
        out["_note"] = "Planetary Computer hrefs need a SAS signature; add_data and add_cog_layer sign them on load."
    return out
