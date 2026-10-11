# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import net
from ..core.background import delete_here, run_on_main_thread, take
from ..core.crs_ref import crs_ref
from ..core.links import host_is
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import stac_tools as _stac
from . import volume_guard
from .data_common import built_here, worker_options
from .harvest_view import _extent_dict, _feature_count

_ARCGIS_URL_RE = re.compile(
    r"^(?P<root>.*?/(?P<service>FeatureServer|MapServer|ImageServer))(?:/(?P<layer>\d+)(?P<query>/query)?)?/?$",
    re.IGNORECASE,
)
_ARCGIS_TIMEOUT = 15




_USER_AGENT = net.user_agent()
_RASTER_MEDIA = ("geotiff", "image/tiff", "image/jp2", "jpeg2000", "x-hdf", "netcdf", "zarr")
_PREVIEW_MEDIA = ("image/png", "image/jpeg", "image/jpg", "image/webp")


def register_harvest_remote_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="add_arcgis_rest_layer",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the ArcGIS layer[ {name}]"),
        input_schema={
            "type": "object",
            "properties": {
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


                "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                "where": {"type": "string"},
            },
            "required": ["url"],
        },
        handler=_add_arcgis_rest_layer,
        background=True,
    ))

    registry.register(Tool(
        name="get_stac_item_assets",
        danger="read",
        catalog=True,
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

    out = {
        "layer_id": layer.id(),
        "layer_name": layer.name(),
        "kind": kind,
        "provider": layer.providerType(),
        "url": url,
        "crs": crs_ref(layer.crs()),
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
        except Exception:  # nosec B110
            out["geometry_type"] = str(layer.geometryType())
        out["fields"] = [f.name() for f in layer.fields()][:40]
    elif isinstance(layer, QgsRasterLayer):
        out["band_count"] = layer.bandCount()
    return out





def _arcgis_parse(url: str):






    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    match = _ARCGIS_URL_RE.match(parts.path)
    if not match or (match.group("query") and match.group("service").lower() == "imageserver"):
        return None
    root = f"{parts.scheme}://{parts.netloc}{match.group('root')}"
    layer_id = match.group("layer")
    clean = f"{root}/{layer_id}" if layer_id is not None else root
    return root, match.group("service").lower(), layer_id, clean


def _arcgis_query_params(url: str) -> tuple[str, str, tuple | None]:





    parts = urllib.parse.urlsplit(url.strip())
    match = _ARCGIS_URL_RE.match(parts.path)
    query = {key.lower(): value for key, value in urllib.parse.parse_qsl(parts.query)}
    is_query = bool(match and match.group("query"))
    where = query.get("where", "").strip() if is_query else ""
    envelope = None
    raw = query.get("geometry", "").strip() if is_query else ""
    if raw and query.get("geometrytype", "esriGeometryEnvelope").strip().lower() == "esrigeometryenvelope":
        wkid = None
        try:
            if raw.startswith("{"):
                shape = json.loads(raw)
                corners = [float(shape[key]) for key in ("xmin", "ymin", "xmax", "ymax")]
                reference = shape.get("spatialReference") or {}
                wkid = reference.get("latestWkid") or reference.get("wkid")
            else:
                corners = [float(part) for part in raw.split(",")]
            reference = query.get("insr", "").strip()
            if reference.startswith("{"):
                reference = str((json.loads(reference) or {}).get("wkid") or "")
            wkid = int(reference) if reference.isdigit() else (int(wkid) if wkid else None)
        except (ValueError, TypeError, KeyError, AttributeError):
            corners = []
        if len(corners) == 4 and corners[0] < corners[2] and corners[1] < corners[3]:
            envelope = (*corners, wkid)
    return ("" if where == "1=1" else where), query.get("token", "").strip(), envelope


def _box_from(envelope: tuple, crs, context) -> list | None:



    xmin, ymin, xmax, ymax, wkid = envelope
    source = QgsCoordinateReferenceSystem(f"EPSG:{wkid}") if wkid else crs
    if source is None or not source.isValid():
        return None
    rect = QgsRectangle(xmin, ymin, xmax, ymax)
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    if source != wgs84:
        try:
            rect = QgsCoordinateTransform(source, wgs84, context).transformBoundingBox(rect)
        except Exception:  # noqa: BLE001
            return None
    return [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()]


def _box_in(box: list, crs, context=None) -> list:



    rect = QgsRectangle(box[0], box[1], box[2], box[3])
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    if crs.isValid() and crs != wgs84:
        rect = QgsCoordinateTransform(wgs84, crs, context if context is not None
                                      else QgsProject.instance()).transformBoundingBox(rect)
    return [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()]


_ARCGIS_MAX_BYTES = 4 * 1024 * 1024
_TOTAL_TIMEOUT_FACTOR = 3
_CACHE_ARCGIS_S = 300


def _arcgis_describe(url: str, token: str | None):


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
    except urllib.error.HTTPError:


        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:




        reason = getattr(exc, "reason", exc)
        if (isinstance(exc, (net.FetchDeadline, net.NetworkUnreachable))
                or isinstance(reason, (socket.timeout, TimeoutError))
                or net._never_reached_a_server(exc)):
            return {"_unreachable": str(reason)[:160]}
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
            hint="arcgis_url_shape",
            url=str(args.get("url") or ""),
        )
    root, service, layer_id, url = parsed
    url_where, url_token, url_envelope = _arcgis_query_params(str(args.get("url") or ""))
    token = str(args.get("token") or "").strip() or url_token or None
    where = str(args.get("where") or "").strip() or url_where
    raw_box = args.get("bbox")
    box = _stac._parse_bbox(raw_box) if raw_box not in (None, "", [], {}) else None
    if raw_box not in (None, "", [], {}) and box is None:
        return tool_error("That bbox is not a usable box.", "INVALID_ARGS",
                          "[west, south, east, north] in EPSG:4326, west below east, south below north.")
    kind = str(args.get("kind") or "auto").lower()
    if kind == "auto":
        kind = "feature" if (service == "featureserver" or layer_id is not None) else "map"





        if service == "featureserver" and layer_id is None:
            root_info = _arcgis_describe(root, token)
            layers = (root_info or {}).get("layers") or []
            if len(layers) == 1 and isinstance(layers[0], dict):
                candidate = layers[0].get("id")
                if isinstance(candidate, int) or (isinstance(candidate, str) and candidate.isdigit()):
                    layer_id = str(candidate)
                    url = f"{root}/{layer_id}"
    if kind == "map" and where:
        return tool_error("A where clause selects features; a map layer draws the whole service as images.",
                          "INVALID_ARGS",
                          "kind feature with a /<id> layer URL takes where; kind map does not.")
    if kind == "feature" and layer_id is None:
        info = _arcgis_describe(root, token)
        layers = (info or {}).get("layers") or []
        listing = ", ".join(
            f"{entry.get('id')}: {entry.get('name')}" for entry in layers[:15] if isinstance(entry, dict)
        )
        return tool_error(
            "A feature layer needs the layer id at the end of the URL.",
            "INVALID_ARGS",
            hint="arcgis_layer_id_missing",
            variant="listed" if listing else "",
            layers=listing,
            url=url,
        )

    info = _arcgis_describe(url, token)
    if info and info.get("_unreachable"):
        return tool_error(f"{url} sent no answer to its description request ({info['_unreachable']}), so "
                          "no layer was opened.", "NETWORK_ERROR",
                          "The host may be down or slow right now.")
    if info and isinstance(info.get("error"), dict):
        err = info["error"]
        code = err.get("code")
        detail = "; ".join(str(d) for d in (err.get("details") or []) if d)
        message = f"The service answered {code}: {err.get('message')}" + (f" ({detail})" if detail else "")
        return tool_error(message, "ARCGIS_REFUSED", hint="arcgis_refused",
                          variant="token" if code in (498, 499) else "", service_code=code, url=url)

    name = _arcgis_name(args, info, root, layer_id)
    crs = str(args.get("crs") or "").strip()




    def _native_crs():

        native = QgsCoordinateReferenceSystem(_arcgis_wkid(info or {}) or "")
        if native.isValid():
            return native

        probe = QgsDataSourceUri()
        probe.setParam("url", url)
        if token:
            probe.setParam("token", token)
        probe.setSql("1=0")

        probed = QgsVectorLayer(probe.uri(False), "probe", "arcgisfeatureserver", worker_options())
        try:
            return probed.crs()
        finally:
            delete_here(probed)


    context = (run_on_main_thread(lambda: QgsProject.instance().transformContext(), timeout=10)
               if (box is not None or url_envelope is not None) and kind == "feature" else None)
    if box is None and url_envelope is not None and kind == "feature":

        box = _box_from(url_envelope, QgsCoordinateReferenceSystem(_arcgis_wkid(info or {}) or ""), context)

    def _feature_layer(options):
        uri = QgsDataSourceUri()
        uri.setParam("url", url)




        known = crs or _arcgis_wkid(info or {})
        if known:
            uri.setParam("crs", known)
        if box is not None:


            uri.setParam("bbox", ",".join(repr(part) for part in _box_in(box, _native_crs(), context)))
        if token:
            uri.setParam("token", token)
        if where:
            uri.setSql(where)
        return QgsVectorLayer(uri.uri(False), name, "arcgisfeatureserver", options)

    built = built_here(lambda: _feature_layer(worker_options())) if kind == "feature" else None

    def _create():
        uri = QgsDataSourceUri()
        if kind == "feature":
            layer = take(built)
            if layer is None:
                layer = _feature_layer(QgsVectorLayer.LayerOptions())
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
            except Exception:  # nosec B110
                pass
            return tool_error(
                f"QGIS could not open the {kind} layer at {url}." + (f" Provider: {reason}" if reason else ""),
                "INVALID_ARGS",
                hint="arcgis_layer_not_opened", variant=kind, kind=kind, url=url, provider_reason=reason,
            )
        if kind == "map" and layer.extent().isEmpty():


            return tool_error(
                f"The map service at {url} did not describe itself (no extent), so the layer would draw "
                "nothing; it was not added.", "EXECUTION_FAILED",
                hint="arcgis_map_no_extent", variant="" if info else "silent", url=url)
        what = "The ArcGIS service answer"
        if kind == "feature" and (box is not None or where):
            what = ("The ArcGIS layer" + (" inside the box" if box is not None else "")
                    + (" under that where" if where else ""))

        refused = volume_guard.too_many_features(layer, args, what, held="add_arcgis_rest_layer")
        if refused:
            return refused
        QgsProject.instance().addMapLayer(layer)
        return _describe_added(layer, kind, url)

    out = run_on_main_thread(_create, timeout=120)
    if out.get("_error"):
        return out
    out["service"] = service
    if kind == "feature":
        if box is not None:
            out["bbox"] = [round(part, 6) for part in box]
        if where:
            out["where"] = where
        if out.get("feature_count") == 0 and (box is not None or where):
            out["warning"] = "The layer loaded and holds nothing %s." % (
                "inside the box" if box is not None else "under that where clause")
            out["suggestion_hint"] = "arcgis_layer_empty"
    elif box is not None:
        out["note"] = "The bbox was not applied: this is a map layer."
        out["note_hint"] = "arcgis_map_bbox_unused"
        out["bbox"] = [round(part, 6) for part in box]
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
    if host_is(href, "blob.core.windows.net") and "sig=" not in href.lower():
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
                "item_url, or collection + item_id, is needed (plus stac_url off Planetary "
                "Computer).",
                "INVALID_ARGS",
                hint="stac_item_args_missing",
            )
        item_url = (
            f"{_stac._stac_root(args)}/collections/{urllib.parse.quote(collection)}"
            f"/items/{urllib.parse.quote(item_id)}"
        )
    try:
        item = _stac._stac_get(item_url)
    except net.FetchCancelled:


        return tool_error("The run was stopped.", "CANCELLED", "The user stopped the run.")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return tool_error(
            f"Failed to fetch the STAC item: {e}",
            "STAC_FETCH_FAILED",
            hint="stac_item_fetch_failed",
            item_url=item_url,
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
        "next": "Assets are listed.",
        "next_hint": "stac_item_next",
    }
    if any(r.get("needs_signing") for r in rows):
        out["_note"] = "Some asset hrefs need a SAS signature."
        out["_note_hint"] = "stac_sas_signing"
    return out
