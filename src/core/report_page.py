# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

import base64
import html as _html
import json
import os
import re
import urllib.parse
import urllib.request



IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PAGE_BYTES = 16 * 1024 * 1024
FIGURE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_IMG_OPEN = re.compile(r"<img(?=[ \t\n\f\r/>])", re.IGNORECASE)

_HEAD_OPEN = re.compile(r"<head\b[^<>]*>", re.IGNORECASE)
_BODY_CLOSE = re.compile(r"</body\s*>", re.IGNORECASE)
_HTML_CLOSE = re.compile(r"</html\s*>", re.IGNORECASE)
_HAS_CHARSET = re.compile(r"<meta\b[^<>]*\bcharset\s*=", re.IGNORECASE)
_HAS_VIEWPORT = re.compile(r"<meta\b[^<>]*\bname\s*=\s*[\"']viewport[\"']", re.IGNORECASE)
_HAS_TITLE = re.compile(r"<title\b", re.IGNORECASE)
_HAS_DOCTYPE = re.compile(r"^\s*<!doctype\b", re.IGNORECASE)
_HAS_HTML = re.compile(r"<html\b", re.IGNORECASE)
_LEADING_DOCTYPE = re.compile(r"^\s*<!doctype\b[^>]*>", re.IGNORECASE)

_ATTRIBUTE = re.compile(r"[ \t\n\f\r/]*([^ \t\n\f\r/>][^ \t\n\f\r/>=]*)[ \t\n\f\r]*(=[ \t\n\f\r]*)?")
_UNQUOTED_END = re.compile(r"[ \t\n\f\r>]")



SCRIPT_HOSTS = ("https://cdnjs.cloudflare.com", "https://cdn.jsdelivr.net/npm/")
STYLE_HOSTS = ("https://fonts.googleapis.com",) + SCRIPT_HOSTS
FONT_HOSTS = ("https://fonts.gstatic.com",)


def _file_url_path(url: str) -> str:






    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc if parsed.netloc.lower() not in ("", "localhost") else ""
    return urllib.request.url2pathname(("//" + host if host else "") + parsed.path)


def data_uri(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


class _Walk:




    def __init__(self):
        self.read: set[int] = set()
        self.run = (0, 0)


def _attributes(text: str, index: int, walk: _Walk):



    while index not in walk.read:
        attribute = _ATTRIBUTE.match(text, index)
        if attribute is None:
            break
        walk.read.add(index)
        name, start = attribute.group(1).lower(), attribute.end()
        if attribute.group(2) is None:
            index = start
            yield name, start, start, False
        elif text[start:start + 1] in ('"', "'"):
            close = text.find(text[start], start + 1)
            index = close + 1 if close >= 0 else len(text)
            yield name, start + 1, close if close >= 0 else index, close >= 0
        else:
            if not walk.run[0] <= start < walk.run[1]:
                stop = _UNQUOTED_END.search(text, start)
                walk.run = (start, stop.start() if stop else len(text))
            index = walk.run[1]
            yield name, start, index, False


def _img_sources(page: str) -> list[tuple[int, int]]:

    spans: list[tuple[int, int]] = []
    walk = _Walk()
    cursor = 0
    while True:
        start = _IMG_OPEN.search(page, cursor)
        if start is None:
            break
        cursor = start.end()
        for name, value_start, value_end, quoted in _attributes(page, cursor, walk):
            if name == "src":
                if quoted:
                    spans.append((value_start, value_end))
                    cursor = value_end
                break
    return spans


def figure_ids(page: str) -> list[str]:

    seen: list[str] = []
    for start, end in _img_sources(page):
        src = page[start:end].strip()
        if src.lower().startswith("figure:"):
            name = src[len("figure:"):].strip()
            if name and name not in seen:
                seen.append(name)
    return seen


def embed_images(page: str, figures: dict[str, tuple[str, bytes]], base_dir: str,
                 refusal=None) -> tuple[str, dict]:










    report = {"figures_placed": [], "figures_missing": [], "files_embedded": [], "files_skipped": []}

    def _swap(src: str) -> str:
        text = src.strip()
        lower = text.lower()
        if lower.startswith("figure:"):
            name = text[len("figure:"):].strip()
            if name in figures:
                mime, raw = figures[name]
                if name not in report["figures_placed"]:
                    report["figures_placed"].append(name)
                return data_uri(mime, raw)
            if name not in report["figures_missing"]:
                report["figures_missing"].append(name)
            return src
        if not text or lower.startswith(("data:", "http://", "https://", "//", "blob:", "#")):
            return src
        candidate = _html.unescape(text)
        if candidate.lower().startswith("file:"):
            candidate = _file_url_path(candidate)
        path = candidate if os.path.isabs(candidate) else os.path.join(base_dir, candidate)
        mime = IMAGE_MIME.get(os.path.splitext(path)[1].lower())
        if not mime:
            return src
        refused = refusal(path) if refusal is not None else None
        if refused:
            report["files_skipped"].append({"src": text, "why": refused})
            return src
        if not os.path.isfile(path):
            report["files_skipped"].append({"src": text, "why": "no such file"})
            return src
        try:
            size = os.path.getsize(path)
            if size > MAX_IMAGE_BYTES:
                report["files_skipped"].append({"src": text, "why": f"{size} bytes, over {MAX_IMAGE_BYTES}"})
                return src
            with open(path, "rb") as stream:
                raw = stream.read()
        except OSError as exc:
            report["files_skipped"].append({"src": text, "why": str(exc)[:120]})
            return src
        report["files_embedded"].append(path)
        return data_uri(mime, raw)

    pieces, position = [], 0
    for start, end in _img_sources(page):
        pieces += [page[position:start], _swap(page[start:end])]
        position = end
    return "".join(pieces) + page[position:], report


_TAG_OPEN = re.compile(r"<(meta|link)(?=[ \t\n\f\r/>]|\Z)", re.IGNORECASE)
_RESOURCE_HINT = re.compile(r"\b(?:dns-prefetch|preconnect|prefetch|prerender)\b", re.IGNORECASE)

def _leaves_the_page(kind: str, name: str, value: str) -> bool:
    value = _html.unescape(value)
    if kind == "meta":
        return name == "http-equiv" and "refresh" in value.lower()
    return name == "rel" and _RESOURCE_HINT.search(value) is not None


def _leaving_tag_end(text: str, index: int, kind: str, walk: _Walk) -> int | None:




    if not any(_leaves_the_page(kind, name, text[start:end]) for name, start, end, _ in _attributes(text, index, walk)):
        return None
    end = index
    for _, _, end, quoted in _attributes(text, index, _Walk()):
        end += quoted
    close = text.find(">", end)
    return close + 1 if close >= 0 else len(text)


def _without_refresh(text: str) -> str:









    while True:
        kept, position, cursor, walks = [], 0, 0, {"meta": _Walk(), "link": _Walk()}
        while True:
            start = _TAG_OPEN.search(text, cursor)
            if start is None:
                break
            cursor = start.end()
            kind = start.group(1).lower()
            end = _leaving_tag_end(text, cursor, kind, walks[kind])
            if end is not None:
                kept.append(text[position:start.start()])
                position = cursor = end
        if not position:
            return text
        kept.append(text[position:])
        text = "".join(kept)


_SCRIPT_OPEN = re.compile(r"<script\b", re.IGNORECASE)
_SCRIPT_CLOSE = re.compile(r"</script\s*>", re.IGNORECASE)
_HANDLER_ATTR = re.compile(r"""\son[a-z]+\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
_JS_URL_ATTR = re.compile(r"""\b(?:href|src|action)\s*=\s*(["'])\s*javascript:(.*?)\1""", re.IGNORECASE | re.DOTALL)
_ADDRESS = re.compile(r"""https?://[^\s"'`<>\\)]+""", re.IGNORECASE)


def _script_bodies(text: str) -> list[str]:

    bodies, cursor = [], 0
    while True:
        start = _SCRIPT_OPEN.search(text, cursor)
        if start is None:
            break
        opened = text.find(">", start.end())
        close = _SCRIPT_CLOSE.search(text, opened + 1) if opened >= 0 else None
        if close is None:
            break
        bodies.append(text[opened + 1:close.start()])
        cursor = close.end()
    return bodies


def active_urls(page: str) -> list[str]:






    text = str(page or "")
    code = _script_bodies(text)
    code += [m.group(2) for m in _HANDLER_ATTR.finditer(text)]
    code += [m.group(2) for m in _JS_URL_ATTR.finditer(text)]
    found: list[str] = []
    for chunk in code:
        for match in _ADDRESS.finditer(_html.unescape(chunk)):
            url = match.group(0).rstrip(".,;")
            if url not in found:
                found.append(url)
    return found


def as_document(page: str, title: str) -> str:





    text = _without_refresh(page.lstrip("\ufeff"))
    if not _HAS_HTML.search(text):
        text = (f'<html lang="en"><head><title>{_html.escape(title)}</title></head>'
                f"<body>{text}</body></html>")
    if not _HAS_DOCTYPE.match(text):
        text = "<!doctype html>\n" + text
    head_tags = []
    if not _HAS_CHARSET.search(text):
        head_tags.append('<meta charset="utf-8">')
    if not _HAS_VIEWPORT.search(text):
        head_tags.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    if not _HAS_TITLE.search(text):
        head_tags.append(f"<title>{_html.escape(title)}</title>")
    if head_tags:
        insert = "".join(head_tags)
        match = _HEAD_OPEN.search(text)
        if match:
            text = text[:match.end()] + insert + text[match.end():]
        else:
            match = _HAS_HTML.search(text)
            end = text.find(">", match.start()) + 1
            text = text[:end] + "<head>" + insert + "</head>" + text[end:]
    return text


def policy(port: int | None) -> str:

    connect = f"http://127.0.0.1:{int(port)}" if port else "'none'"
    return "; ".join((
        "default-src 'none'",
        "img-src data: blob:",
        "style-src 'unsafe-inline' " + " ".join(STYLE_HOSTS),
        "font-src data: " + " ".join(FONT_HOSTS),


        "script-src 'unsafe-inline' 'unsafe-eval' " + " ".join(SCRIPT_HOSTS),
        f"connect-src {connect}",
        "form-action 'none'",
        "base-uri 'none'",
    ))


def with_policy(page: str, port: int | None) -> str:





    meta = f'<meta http-equiv="Content-Security-Policy" content="{policy(port)}">'
    match = _LEADING_DOCTYPE.match(page)
    if match is None:
        return meta + page
    return page[:match.end()] + meta + page[match.end():]


_POLICY_META = re.compile(
    r'\A\s*<!doctype\b[^>]*>\s*<meta http-equiv="Content-Security-Policy" content="([^"]*)">', re.IGNORECASE)


def is_report_file(path: str) -> bool:






    try:
        with open(path, "rb") as stream:
            head = stream.read(4096).decode("utf-8", "ignore")
    except OSError:
        return False
    match = _POLICY_META.match(head.lstrip("\ufeff"))
    if match is None:
        return False
    bridge = re.search(r"connect-src http://127\.0\.0\.1:(\d{1,5})(?:;|$)", match.group(1))
    return match.group(1) == policy(int(bridge.group(1)) if bridge else None)


def with_bridge(page: str, port: int, token: str, labels: dict[str, str]) -> str:





    settings = json.dumps({"port": int(port), "token": str(token), "labels": labels}, ensure_ascii=True)
    script = BRIDGE_SCRIPT.replace("__SETTINGS__", settings)
    match = _BODY_CLOSE.search(page)
    if match is None:
        match = _HTML_CLOSE.search(page)
    if match is None:
        return page + script
    return page[:match.start()] + script + page[match.start():]






BRIDGE_SCRIPT = """
<script data-terralab-bridge>
(function () {
  var cfg = __SETTINGS__;
  var base = "http://127.0.0.1:" + cfg.port + "/qgis";
  var nodes = document.querySelectorAll("[data-qgis-layer]");
  if (!nodes.length) { return; }
  var style = document.createElement("style");
  style.textContent = ".qgis-link{display:inline-block;margin-left:.4em;padding:.05em .45em;" +
    "border:1px solid currentColor;" +
    "border-radius:.6em;font:inherit;font-size:.78em;line-height:1.5;color:inherit;background:transparent;" +
    "opacity:.75;cursor:pointer;vertical-align:baseline;white-space:nowrap}" +
    ".qgis-link:hover,.qgis-link:focus-visible{opacity:1}.qgis-link[disabled]{opacity:.4;cursor:default}" +
    "@media print{.qgis-link{display:none}}";
  document.head.appendChild(style);
  function call(params, done) {
    var query = Object.keys(params).map(function (k) {
      return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]);
    }).join("&");
    var ctl = ("AbortController" in window) ? new AbortController() : null;
    var timer = ctl ? setTimeout(function () { ctl.abort(); }, 4000) : null;
    fetch(base + "?token=" + encodeURIComponent(cfg.token) + "&" + query, {signal: ctl ? ctl.signal : undefined})
      .then(function (r) { return r.json(); })
      .then(function (body) { done(body && body.ok ? body : null); })
      .catch(function () { done(null); })
      .then(function () { if (timer) { clearTimeout(timer); } });
  }
  var links = [];
  Array.prototype.forEach.call(nodes, function (node) {
    var layer = node.getAttribute("data-qgis-layer");
    if (!layer) { return; }
    var link = document.createElement("button");
    link.type = "button";
    link.className = "qgis-link";
    link.textContent = cfg.labels.show;
    link.title = cfg.labels.show;
    link.addEventListener("click", function () {
      link.disabled = true;
      var params = {"do": "zoom", "layer": layer};
      var fids = node.getAttribute("data-qgis-fids");
      if (fids) { params.fids = fids; }
      call(params, function (body) {
        link.disabled = false;
        link.textContent = body ? cfg.labels.done : cfg.labels.away;
        setTimeout(function () { link.textContent = cfg.labels.show; }, 2500);
      });
    });
    node.insertAdjacentElement("afterend", link);
    links.push(link);
  });
  call({"do": "ping"}, function (body) {
    if (!body) { links.forEach(function (l) { l.title = cfg.labels.away; }); }
  });
})();
</script>
"""
