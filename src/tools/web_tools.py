# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic web tools: read a JSON document or a page as text, turn a JSON list into a point layer."""








from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)

from ..core import limits, net
from ..core.qt_compat import field_type
from ..core.security import is_local_url
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import volume_guard
from .data_tools import _USER_AGENT, _run_on_main_thread

_TIMEOUT_S = 30
_MAX_BYTES = 25 * 1024 * 1024

_TOTAL_TIMEOUT_S = 90

_CACHE_S = 300



_MAX_RECORDS = limits.MAX_FEATURES_PER_CALL
_MAX_FIELDS = 30
_DEFAULT_MAX_CHARS = 6000
_MAX_CHARS_CAP = 40000
_ACCEPT = "application/json, application/geo+json;q=0.9, */*;q=0.5"


def register_web_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="fetch_json",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "path": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 200, "maximum": _MAX_CHARS_CAP},
            },
            "required": ["url"],
        },
        handler=_fetch_json,
        background=True,
    ))

    registry.register(Tool(
        name="fetch_text",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "section": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 200, "maximum": _MAX_CHARS_CAP},
            },
            "required": ["url"],
        },
        handler=_fetch_text,
        background=True,
    ))

    registry.register(Tool(
        name="add_points_from_json",
        input_schema={
            "type": "object",
            "properties": {
                "confirm_large": {"type": "boolean"},
                "url": {"type": "string"},
                "records_path": {"type": "string"},
                "lat_key": {"type": "string"},
                "lon_key": {"type": "string"},
                "fields": {
                    "type": "array", "items": {"type": "string"}, "maxItems": _MAX_FIELDS,
                },
                "name": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_RECORDS},
            },
            "required": ["url", "lat_key", "lon_key"],
        },
        handler=_add_points_from_json,
        background=True,
    ))





def _fetch(url: str) -> tuple[object | None, dict | None]:
    """(document, None) or (None, error result)."""
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return None, tool_error("URL must use http or https.", "INVALID_ARGS", "Pass an http(s) URL.")
    if is_local_url(url):
        return None, tool_error("URLs on this machine or on link-local addresses are not fetched.", "PERMISSION_DENIED",
                                "Use a public URL, or ask the user to export the data to a file and load it "
                                "with add_data.")
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": _ACCEPT})
    try:
        raw = net.fetch(req, timeout=_TIMEOUT_S, max_bytes=_MAX_BYTES,
                        total_timeout=_TOTAL_TIMEOUT_S, cache_ttl=_CACHE_S).body
    except net.FetchTooLarge:
        return None, tool_error(f"The document is over {_MAX_BYTES // (1024 * 1024)} MB.", "INVALID_ARGS",
                                "Ask the API for fewer records, a bbox, or one page at a time.")
    except net.FetchDeadline as e:
        return None, tool_error(f"{url} did not answer in time: {e}", "NETWORK_ERROR",
                                "Ask the API for fewer records, or try again later.")
    except net.FetchCancelled:
        return None, tool_error("The run was stopped.", "CANCELLED", "Wait for the next user message.")
    except urllib.error.HTTPError as e:
        return None, tool_error(f"HTTP {e.code} from {url}", "HTTP_ERROR",
                                "Check the URL and its parameters; some APIs need a smaller area or a key.")
    except (urllib.error.URLError, OSError) as e:
        return None, tool_error(f"Could not fetch {url}: {e}", "NETWORK_ERROR", "Check the URL and the connection.")
    try:
        return json.loads(raw.decode("utf-8", errors="replace")), None
    except ValueError as e:
        return None, tool_error(f"The response is not JSON: {e}", "NOT_JSON",
                                "inspect_data_source reads other formats; add_data loads GeoJSON directly.")


def _walk(value, path: str):
    """Follow a dotted path with integer indexes for lists; None when a step is missing."""
    for step in [p for p in (path or "").split(".") if p != ""]:
        if isinstance(value, dict):
            value = value.get(step)
        elif isinstance(value, (list, tuple)):
            try:
                value = value[int(step)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return value


def _fetch_json(args: dict) -> dict:
    url = str(args.get("url") or "").strip()
    doc, error = _fetch(url)
    if error is not None:
        return error
    path = str(args.get("path") or "")
    part = _walk(doc, path) if path else doc
    if path and part is None:
        return tool_error(f"Nothing at path '{path}'.", "INVALID_ARGS",
                          "Call again without path to see the document's shape.")
    try:
        max_chars = max(200, min(int(args.get("max_chars") or _DEFAULT_MAX_CHARS), _MAX_CHARS_CAP))
    except (TypeError, ValueError):
        max_chars = _DEFAULT_MAX_CHARS
    text = json.dumps(part, ensure_ascii=False, separators=(",", ":"), default=str)
    out: dict = {"url": url, "chars": len(text)}
    if path:
        out["path"] = path
    if isinstance(part, list):
        out["records"] = len(part)
    elif isinstance(part, dict):
        out["keys"] = list(part.keys())[:60]
    if len(text) > max_chars:
        out["json"] = text[:max_chars]
        out["truncated"] = True
        out["hint"] = "Truncated: pass path to read one part, or a larger max_chars."
    else:
        out["json"] = part
    return out




_ACCEPT_TEXT = "text/html, text/plain;q=0.9, text/markdown;q=0.9, */*;q=0.5"
_DROP_TAGS_RE = re.compile(r"<(script|style|nav|header|footer|noscript|svg)\b.*?</\1\s*>",
                           re.IGNORECASE | re.DOTALL)




_HIDDEN_RE = re.compile(r"<([a-z][a-z0-9]*)\b[^>]*?(?:\shidden(?=[\s>=/])|aria-hidden=[\"']?true"
                        r"|display\s*:\s*none|visibility\s*:\s*hidden)[^>]*>.*?</\1\s*>",
                        re.IGNORECASE | re.DOTALL)
_BLOCK_TAGS_RE = re.compile(r"</?(p|div|br|li|ul|ol|tr|td|th|h[1-6]|section|article|pre|blockquote|dt|dd|table)"
                            r"\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_ANCHOR_RE = r"""(?:id|name)\s*=\s*["']{anchor}["']"""


def _fetch_raw(url: str) -> tuple[bytes | None, str, dict | None]:
    """(body, content type, None) or (None, "", error result)."""
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return None, "", tool_error("URL must use http or https.", "INVALID_ARGS", "Pass an http(s) URL.")
    if is_local_url(url):
        return None, "", tool_error("URLs on this machine or on link-local addresses are not fetched.",
                                    "PERMISSION_DENIED",
                                    "Use a public URL, or ask the user to export the data to a file and load it "
                                    "with add_data.")
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": _ACCEPT_TEXT})
    try:
        answer = net.fetch(req, timeout=_TIMEOUT_S, max_bytes=_MAX_BYTES,
                           total_timeout=_TOTAL_TIMEOUT_S, cache_ttl=_CACHE_S)
        return answer.body, str(answer.headers.get("content-type") or ""), None
    except net.FetchTooLarge:
        return None, "", tool_error(f"The document is over {_MAX_BYTES // (1024 * 1024)} MB.",
                                    "INVALID_ARGS", "Ask for a smaller page or one section of it.")
    except net.FetchDeadline as e:
        return None, "", tool_error(f"{url} did not answer in time: {e}", "NETWORK_ERROR", "Try again later.")
    except net.FetchCancelled:
        return None, "", tool_error("The run was stopped.", "CANCELLED", "Wait for the next user message.")
    except urllib.error.HTTPError as e:
        return None, "", tool_error(f"HTTP {e.code} from {url}", "HTTP_ERROR", "Check the URL.")
    except (urllib.error.URLError, OSError) as e:
        return None, "", tool_error(f"Could not fetch {url}: {e}", "NETWORK_ERROR",
                                    "Check the URL and the connection.")


def _html_to_text(html: str) -> str:
    text = _DROP_TAGS_RE.sub(" ", html)
    text = _HIDDEN_RE.sub(" ", text)
    text = _BLOCK_TAGS_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = _unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _unescape(text: str) -> str:
    import html

    return html.unescape(text)


def _fetch_text(args: dict) -> dict:
    url = str(args.get("url") or "").strip()
    section = str(args.get("section") or "").strip().lstrip("#")
    if not section and "#" in url:
        url, section = url.split("#", 1)
    raw, content_type, error = _fetch_raw(url)
    if error is not None:
        return error
    if len(raw) > _MAX_BYTES:
        return tool_error(f"The page is over {_MAX_BYTES // (1024 * 1024)} MB.", "INVALID_ARGS",
                          "Read one section, or a smaller page.")
    body = raw.decode("utf-8", errors="replace")
    is_html = "html" in content_type.lower() or body.lstrip()[:200].lower().startswith(("<!doctype", "<html"))
    started_at_section = False
    if is_html:
        if section:
            match = re.search(_ANCHOR_RE.format(anchor=re.escape(section)), body, re.IGNORECASE)
            if match is not None:
                tag_start = body.rfind("<", 0, match.start())
                body = body[tag_start if tag_start >= 0 else match.start():]
                started_at_section = True
        text = _html_to_text(body)
    else:
        text = body
    if section and not started_at_section:
        index = text.lower().find(section.replace("-", " ").lower())
        if index >= 0:
            text = text[index:]
            started_at_section = True
    try:
        max_chars = max(200, min(int(args.get("max_chars") or _DEFAULT_MAX_CHARS), _MAX_CHARS_CAP))
    except (TypeError, ValueError):
        max_chars = _DEFAULT_MAX_CHARS
    out: dict = {"url": url, "chars": len(text), "content_type": content_type.split(";")[0].strip()}
    if section:
        out["section"] = section
        out["section_found"] = started_at_section
    if len(text) > max_chars:
        out["text"] = text[:max_chars]
        out["truncated"] = True
        out["hint"] = "Truncated: pass section to start at a heading, or a larger max_chars."
    else:
        out["text"] = text
    return out





def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _attribute(value):
    if value is None or isinstance(value, (int, float, str)) and not isinstance(value, bool):
        return value
    if isinstance(value, bool):
        return int(value)
    return json.dumps(value, ensure_ascii=False, default=str)[:500]


def _column_type(values: list):
    """Int when every non-null value is a whole number, Double when all are numbers, else String."""
    seen = [v for v in values if v is not None and v != ""]
    if not seen:
        return field_type("String")
    if all(isinstance(v, int) and not isinstance(v, bool) for v in seen):
        return field_type("LongLong")
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seen):
        return field_type("Double")
    return field_type("String")


def _typed(value, kind):
    if value is None or value == "":
        return None
    if kind == field_type("LongLong"):
        return int(value)
    if kind == field_type("Double"):
        return float(value)
    return str(value)


def _field_name(path: str) -> str:
    name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in path.strip()) or "field"
    return ("f_" + name if name[0].isdigit() else name)[:60]


def _add_points_from_json(args: dict) -> dict:
    url = str(args.get("url") or "").strip()
    lat_key = str(args.get("lat_key") or "").strip()
    lon_key = str(args.get("lon_key") or "").strip()
    if not lat_key or not lon_key:
        return tool_error("lat_key and lon_key are required.", "INVALID_ARGS",
                          "Name the record keys holding the latitude and the longitude.")
    doc, error = _fetch(url)
    if error is not None:
        return error
    records = _walk(doc, str(args.get("records_path") or ""))
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        return tool_error("records_path does not point to a list.", "INVALID_ARGS",
                          "fetch_json on the same URL shows the document's keys.")
    try:
        limit = max(1, min(int(args.get("limit") or _MAX_RECORDS), _MAX_RECORDS))
    except (TypeError, ValueError):
        limit = _MAX_RECORDS
    keys = [str(k) for k in (args.get("fields") or []) if str(k).strip()][:_MAX_FIELDS]
    names = []
    for key in keys:
        name = _field_name(key)
        while name in names:
            name += "_"
        names.append(name)
    layer_name = str(args.get("name") or "Points").strip() or "Points"

    rows = []
    skipped = 0
    for record in records[:limit]:
        lat = _number(_walk(record, lat_key))
        lon = _number(_walk(record, lon_key))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            skipped += 1
            continue
        rows.append((lon, lat, [_attribute(_walk(record, key)) for key in keys]))
    if not rows:
        return tool_error("No record had a valid latitude and longitude.", "INVALID_ARGS",
                          "fetch_json on the URL shows the record shape; check lat_key and lon_key.")

    types = [_column_type([row[2][i] for row in rows]) for i in range(len(keys))]

    def _create():
        layer = QgsVectorLayer("Point?crs=EPSG:4326", layer_name, "memory")
        provider = layer.dataProvider()
        fields = QgsFields()
        for name, kind in zip(names, types):
            fields.append(QgsField(name, kind))
        provider.addAttributes(list(fields))
        layer.updateFields()
        features = []
        for lon, lat, values in rows:
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
            feature.setAttributes([_typed(v, kind) for v, kind in zip(values, types)])
            features.append(feature)
        provider.addFeatures(features)
        layer.updateExtents()
        layer.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        refused = volume_guard.too_many_features(layer, args, "The JSON answer")
        if refused:
            return refused
        QgsProject.instance().addMapLayer(layer)
        extent = layer.extent()
        return {
            "layer_id": layer.id(), "layer_name": layer.name(), "crs": "EPSG:4326",
            "feature_count": layer.featureCount(), "skipped": skipped, "fields": names,
            "records_available": len(records),
            "extent": {"xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
                       "xmax": extent.xMaximum(), "ymax": extent.yMaximum()},
            "note": "Memory layer in EPSG:4326: reproject before measuring; save_layer_to_gpkg keeps it.",
        }

    return _run_on_main_thread(_create, timeout=60)
