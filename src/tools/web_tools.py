# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic web tools: read a JSON document or a page as text, turn a JSON list into a point layer."""








from __future__ import annotations

import json
import math
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib

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

from ..core import limits, links, net
from ..core.qt_compat import field_type
from ..core.security import is_local_url
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import volume_guard
from .data_tools import _USER_AGENT, _run_on_main_thread, expand_link

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
        answer = net.fetch(req, timeout=_TIMEOUT_S, max_bytes=_MAX_BYTES,
                           total_timeout=_TOTAL_TIMEOUT_S, cache_ttl=_CACHE_S)
        raw, content_type = answer.body, str(answer.headers.get("content-type") or "")
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





        return json.loads(raw.decode(_charset(raw, content_type), errors="replace")), None
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


def _link_first(url: str) -> tuple[dict, dict | None]:
    """The pasted link resolved, and the whole answer when it is not one address to fetch."""
    link = expand_link(url)
    if link.get("error"):
        return link, link["error"]
    if link["kind"] == "unreachable":
        return link, tool_error(link["note"], "INVALID_ARGS",
                                "Ask the user for the link of the file itself, shared with anyone who has the link.")
    if link["kind"] == "listing":
        files = link.get("files") or []

        text = "\n".join([f"{link['note']} Listed from {link['url']}, {len(files)} files:"] + [
            f"{entry['name']}  {entry['url']}" + (f"  ({entry['size_bytes']} bytes)" if entry.get("size_bytes") else "")
            for entry in files])
        return link, {"url": link["resolved_from"], "resolved_from": link["resolved_from"],
                      "listing_url": link["url"], "file_count": len(files), "layers": files,
                      **{k: link[k] for k in ("licence", "publisher") if link.get(k)},
                      "chars": len(text), "text": text, "note": link["note"]}
    return link, None


def _fetch_json(args: dict) -> dict:
    link, answer = _link_first(str(args.get("url") or "").strip())
    if answer is not None:
        return answer
    url = link["url"]
    if link["kind"] == "inline":
        try:
            doc, error = json.loads(link["inline"]), None
        except ValueError as e:
            doc, error = None, tool_error(f"The data inside the link is not JSON: {e}", "NOT_JSON",
                                          "Ask the user to export it as a file.")
    else:
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
    if link.get("resolved_from"):
        out["resolved_from"] = link["resolved_from"]
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
_DROP_TAGS_RE = re.compile(r"<(script|style|nav|header|footer|noscript|svg|aside|template)\b.*?</\1\s*>",
                           re.IGNORECASE | re.DOTALL)




_HIDDEN_RE = re.compile(r"<([a-z][a-z0-9]*)\b[^>]*?(?:\shidden(?=[\s>=/])|aria-hidden=[\"']?true"
                        r"|display\s*:\s*none|visibility\s*:\s*hidden)[^>]*>.*?</\1\s*>",
                        re.IGNORECASE | re.DOTALL)
_BLOCK_TAGS_RE = re.compile(r"</?(p|div|br|ul|ol|table|thead|tbody|section|article|main|pre|blockquote|dt|dd"
                            r"|figure|figcaption)\b[^>]*>", re.IGNORECASE)
_HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.IGNORECASE | re.DOTALL)
_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[hd]\b[^>]*>(.*?)</t[hd]\s*>", re.IGNORECASE | re.DOTALL)
_ITEM_RE = re.compile(r"<li\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ANCHOR_RE = r"""(?:id|name)\s*=\s*["']{anchor}["']"""
_MAIN_RES = (re.compile(r"<main\b[^>]*>(.*)</main\s*>", re.IGNORECASE | re.DOTALL),
             re.compile(r"<([a-z]+)\b[^>]*\brole\s*=\s*[\"']main[\"'][^>]*>(.*)</\1\s*>", re.IGNORECASE | re.DOTALL),
             re.compile(r"<article\b[^>]*>(.*)</article\s*>", re.IGNORECASE | re.DOTALL))
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_OG_TITLE_RE = re.compile(
    r"<meta[^>]*property\s*=\s*[\"']og:title[\"'][^>]*content\s*=\s*[\"']([^\"']*)", re.IGNORECASE)
_LINK_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"'#][^\"']*)[\"'][^>]*>(.*?)</a\s*>", re.IGNORECASE | re.DOTALL)
_GEO_RE = re.compile(r"<span[^>]*class\s*=\s*[\"']geo[\"'][^>]*>\s*(-?\d+(?:\.\d+)?)\s*[;,]\s*(-?\d+(?:\.\d+)?)",
                     re.IGNORECASE)
_META_CHARSET_RE = re.compile(rb"<meta[^>]*charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_EMBEDDED_JSON_RES = (
    re.compile(r"<script[^>]*id\s*=\s*[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.IGNORECASE | re.DOTALL),
)
_MAX_LINKS = 30

_SERVICE_HINTS = ("service=wms", "service=wfs", "service=wmts", "service=wcs", "/wms", "/wfs", "/wmts", "/ows",
                  "/rest/services", "/geoserver", "/api/", "/download", "/export", "/datasets/", "/dataset/",
                  "/collections", "getcapabilities", "f=geojson", "/resource/", "/files/")

_BINARY_DATA_TYPES = ("application/zip", "application/x-zip", "image/tiff", "application/geopackage",
                      "application/vnd.sqlite3", "application/x-sqlite3", "application/vnd.google-earth.kmz",
                      "application/flatgeobuf", "application/vnd.apache.parquet", "application/x-parquet",
                      "application/geo+json", "application/vnd.geo+json", "application/octet-stream",
                      "application/vnd.pmtiles", "application/vnd.openxmlformats-officedocument")
_BINARY_EXTS = (".zip", ".tif", ".tiff", ".gpkg", ".kmz", ".fgb", ".parquet", ".geoparquet", ".pmtiles",
                ".xlsx", ".ods", ".geojson", ".shp", ".jp2", ".nc", ".laz", ".las", ".gz")


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


def _cell_text(fragment: str) -> str:
    return " ".join(_unescape(_TAG_RE.sub(" ", fragment)).split()).replace("|", "/")


def _html_to_text(html: str) -> str:
    """The page as markdown-ish text: headings as #, list items as -, table rows as | cells |."""
    text = _COMMENT_RE.sub(" ", html)
    text = _DROP_TAGS_RE.sub(" ", text)
    text = _HIDDEN_RE.sub(" ", text)
    text = _HEADING_RE.sub(lambda m: f"\n{'#' * int(m.group(1))} {_cell_text(m.group(2))}\n", text)
    text = _ROW_RE.sub(lambda m: "\n| " + " | ".join(_cell_text(c) for c in _CELL_RE.findall(m.group(1))) + " |\n",
                       text)
    text = _ITEM_RE.sub("\n- ", text)
    text = re.sub(r"</li\s*>", "\n", text, flags=re.IGNORECASE)
    text = _BLOCK_TAGS_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = _unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line in ("-", "| |"):
            continue
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _unescape(text: str) -> str:
    import html

    return html.unescape(text)


def _charset(raw: bytes, content_type: str) -> str:
    """The encoding the answer declares: the header first, then the page's own meta tag."""
    found = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", content_type or "", re.IGNORECASE)
    if not found:
        meta = _META_CHARSET_RE.search(raw[:4096])
        name = meta.group(1).decode("ascii", "ignore") if meta else ""
    else:
        name = found.group(1)
    import codecs

    try:
        return codecs.lookup(name).name if name else "utf-8"
    except LookupError:
        return "utf-8"


def _main_block(body: str) -> str:
    """The page's main content when it marks one (main, role=main, article) and it holds text."""
    for pattern in _MAIN_RES:
        found = pattern.search(body)
        if found:
            block = found.group(found.lastindex)
            if len(_TAG_RE.sub("", block).strip()) >= 200:
                return block
    return ""


def _page_links(body: str, base: str) -> list[dict]:
    """Up to 30 links worth following: data files first, then services and APIs, then this site's pages."""
    host = urllib.parse.urlsplit(base).hostname or ""
    ranked: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for href, label in _LINK_RE.findall(body):
        url = urllib.parse.urljoin(base, _unescape(href.strip()))
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or url in seen:
            continue
        seen.add(url)
        lowered = url.lower()
        ext = links.extension(url)
        if ext in links.DATA_EXTENSIONS and ext != ".json":
            score = 0
        elif any(hint in lowered for hint in _SERVICE_HINTS):
            score = 1
        elif parts.hostname == host and (lowered.endswith((".html", ".htm", ".md", ".pdf", "/")) or "doc" in lowered):
            score = 2
        else:
            continue
        text = " ".join(_unescape(_TAG_RE.sub(" ", label)).split())[:100]
        ranked.append((score, {"text": text or posixpath.basename(parts.path) or url, "url": url}))
    ranked.sort(key=lambda pair: pair[0])
    return [entry for _, entry in ranked[:_MAX_LINKS]]


def _embedded_json(body: str) -> str:
    for pattern in _EMBEDDED_JSON_RES:
        found = pattern.search(body)
        if found and len(found.group(1).strip()) > 40:
            return found.group(1).strip()
    return ""



_PDF_STREAM_RE = re.compile(rb"stream(?:\r\n|\n|\r)(.*?)(?:\r\n|\n|\r)?endstream", re.DOTALL)
_PDF_ARRAY_PART_RE = re.compile(rb"\(((?:\\.|[^\\)])*)\)|(-?\d+(?:\.\d+)?)")
_PDF_TOKEN_RE = re.compile(rb"\(((?:\\.|[^\\)])*)\)\s*(?:Tj|'|\")|\[((?:\\.|[^\\\]])*)\]\s*TJ|\b(?:Td|TD|T\*|Tm)\b")
_PDF_TITLE_RE = re.compile(rb"/Title\s*\(((?:\\.|[^\\)]){1,300})\)")
_PDF_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"", b"f": b"", b"(": b"(", b")": b")", b"\\": b"\\"}
_PDF_MAX_STREAMS = 2000


def _pdf_string(raw: bytes) -> str:
    out = re.sub(rb"\\([nrtbf()\\]|[0-7]{1,3})",
                 lambda m: _PDF_ESCAPES.get(m.group(1)) or bytes([int(m.group(1), 8) & 0xFF]), raw)
    if out.startswith(b"\xfe\xff"):
        return out[2:].decode("utf-16-be", "ignore")
    return out.decode("latin-1", "ignore")


def _pdf_text(raw: bytes) -> tuple[str, str]:
    """(text, title) out of a PDF's Flate or plain content streams, "" when none is readable."""




    title_match = _PDF_TITLE_RE.search(raw)
    title = " ".join(_pdf_string(title_match.group(1)).split()) if title_match else ""
    pieces: list[str] = []
    for index, stream in enumerate(_PDF_STREAM_RE.findall(raw)):
        if index >= _PDF_MAX_STREAMS:
            break
        try:
            data = zlib.decompress(stream)
        except zlib.error:
            data = stream
        if b"BT" not in data:
            continue
        for block in re.findall(rb"BT(.*?)ET", data, re.DOTALL):
            line: list[str] = []

            for token in _PDF_TOKEN_RE.finditer(block):
                literal, array = token.group(1), token.group(2)
                if literal is not None:
                    line.append(_pdf_string(literal))
                elif array is not None:
                    for text, kern in _PDF_ARRAY_PART_RE.findall(array):
                        if text:
                            line.append(_pdf_string(text))
                        elif kern and float(kern) < -200:
                            line.append(" ")
                elif line and line[-1] != "\n":
                    line.append("\n")
            joined = "\n".join(" ".join(part.split()) for part in "".join(line).split("\n") if part.strip())
            if joined:
                pieces.append(joined)
    text = re.sub(r"(?m)^([A-Z])\n(?=[A-Z])", r"\1", "\n".join(pieces))
    letters = sum(ch.isalpha() for ch in text)

    if not text or letters < 0.5 * len(text.replace(" ", "").replace("\n", "")):
        return "", title
    return text, title


def _fetch_text(args: dict) -> dict:


    link, answer = _link_first(str(args.get("url") or "").strip())
    if answer is not None:
        return answer
    section = str(args.get("section") or "").strip().lstrip("#")
    if link["kind"] == "inline":
        text = link["inline"]
        return {"url": link["resolved_from"], "resolved_from": link["resolved_from"], "chars": len(text),
                "content_type": "application/json", "text": text[:_MAX_CHARS_CAP], "note": link["note"]}
    url = link["url"]
    if link["kind"] == "unchanged" and not section and "#" in url:
        url, section = url.split("#", 1)
    raw, content_type, error = _fetch_raw(url)
    if error is not None:
        return error
    if len(raw) > _MAX_BYTES:
        return tool_error(f"The page is over {_MAX_BYTES // (1024 * 1024)} MB.", "INVALID_ARGS",
                          "Read one section, or a smaller page.")
    try:
        max_chars = max(200, min(int(args.get("max_chars") or _DEFAULT_MAX_CHARS), _MAX_CHARS_CAP))
    except (TypeError, ValueError):
        max_chars = _DEFAULT_MAX_CHARS
    mime = content_type.split(";")[0].strip().lower()
    out: dict = {"url": url, "content_type": mime}
    if link.get("resolved_from"):
        out["resolved_from"] = link["resolved_from"]
    ext = links.extension(url)


    head = raw[:512].lstrip()[:15].lower()
    binary = mime.startswith(_BINARY_DATA_TYPES) or (
        ext in _BINARY_EXTS and not head.startswith((b"<!doctype", b"<html")))
    if (binary and not raw.startswith(b"%PDF")) or raw[:4] == b"PK\x03\x04" or raw[:15] == b"SQLite format 3":
        name = posixpath.splitext(posixpath.basename(urllib.parse.urlsplit(url).path))[0] or "dataset"
        out.update({"chars": 0, "text": "", "data_file": True, "size_bytes": len(raw),
                    "call": {"tool": "add_data", "args": {"source": url, "name": name}},
                    "note": "A data file, not a page: call carries the add_data call that loads it."})
        return out

    if mime == "application/pdf" or raw.startswith(b"%PDF"):
        text, title = _pdf_text(raw)
        out["pdf"] = True
        if title:
            out["title"] = title
        if not text:
            out.update({"chars": 0, "text": "",
                        "note": ("A PDF whose text could not be read here: it draws its letters through its own "
                                 "font tables, or it is scanned. Ask the user for the passage they need, or for "
                                 "a web page of the same document.")})
            return out
        body_text = text
    else:
        body = raw.decode(_charset(raw, content_type), errors="replace")
        is_html = "html" in mime or body.lstrip()[:200].lower().startswith(("<!doctype", "<html"))
        if not is_html:
            body_text = body
            if ext in links.DATA_EXTENSIONS:
                out["call"] = {"tool": "add_data", "args": {"source": url}}
        else:
            title = _TITLE_RE.search(body) or _OG_TITLE_RE.search(body)
            if title:
                out["title"] = " ".join(_unescape(_TAG_RE.sub(" ", title.group(1))).split())[:200]
            coordinates = _GEO_RE.search(body)
            if coordinates:
                out["coordinates"] = {"lat": float(coordinates.group(1)), "lon": float(coordinates.group(2))}
            started_at_section = False
            page = body
            if section:
                match = re.search(_ANCHOR_RE.format(anchor=re.escape(section)), body, re.IGNORECASE)
                if match is not None:
                    tag_start = body.rfind("<", 0, match.start())
                    page = body[tag_start if tag_start >= 0 else match.start():]
                    started_at_section = True
            else:
                page = _main_block(body) or body
            body_text = _html_to_text(page)
            found_links = _page_links(page if len(_page_links(page, url)) else body, url)
            if found_links:
                out["links"] = found_links
            if len(body_text) < 200 and "<script" in body.lower():
                embedded = _embedded_json(body)
                if embedded:
                    out["embedded_json"] = embedded[:max_chars]
                    out["note"] = ("The page builds itself with JavaScript; embedded_json is the data it "
                                   "carries for that.")
                else:


                    out["js_rendered"] = True
            if section:
                out["section"] = section
                out["section_found"] = started_at_section
                if not started_at_section:
                    index = _section_index(body_text, section)
                    if index >= 0:
                        body_text = body_text[index:]
                        out["section_found"] = True
            return _cut(out, body_text, max_chars)
    if section:
        out["section"] = section
        index = _section_index(body_text, section)
        out["section_found"] = index >= 0
        if index >= 0:
            body_text = body_text[index:]
    return _cut(out, body_text, max_chars)


def _section_index(text: str, section: str) -> int:
    """Where a section starts in the text: its heading line first, else the first mention of its words."""
    words = " ".join(section.replace("-", " ").replace("_", " ").split()).lower()
    if not words:
        return -1
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.startswith("#") and words in line.lower():
            return offset
        offset += len(line)
    return text.lower().find(words)


def _cut(out: dict, text: str, max_chars: int) -> dict:
    out["chars"] = len(text)
    if len(text) > max_chars:
        out["text"] = text[:max_chars]
        out["truncated"] = True
        out["hint"] = (f"Cut at {max_chars} of {len(text)} characters: pass section to start at a heading, or a "
                       "larger max_chars.")
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
    return None if math.isnan(number) else number


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
    ceiling = limits.current("MAX_FEATURES_PER_CALL")
    try:
        limit = max(1, min(int(args.get("limit") or ceiling), ceiling))
    except (TypeError, ValueError):
        limit = ceiling
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
