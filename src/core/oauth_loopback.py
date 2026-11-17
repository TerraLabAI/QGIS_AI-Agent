# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import base64
import hashlib
import html
import http.server
import json
import os
import secrets
import string
import threading
import urllib.error
import urllib.parse
import urllib.request


def pkce_pair() -> tuple[str, str]:

    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorization_url(auth_uri: str, client_id: str, redirect_uri: str, scopes, challenge: str,
                      state: str, extra: dict | None = None) -> str:
    query = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
             "scope": " ".join(scopes), "code_challenge": challenge, "code_challenge_method": "S256",
             "state": state, **(extra or {})}
    return f"{auth_uri}?{urllib.parse.urlencode(query)}"





_PAGE = string.Template("""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Agent \u00b7 QGIS</title>
<style>
:root{--page:#f7f8fa;--card:#ffffff;--border:#e2e5ea;--ink:#1f2124;--muted:#5b5f66;--faint:#8a8e95;
color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--page:#17181a;--card:#232427;--border:#2e3033;--ink:#f2f3f4;
--muted:#a5a8ad;--faint:#7d8187}}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;display:flex;align-items:center;justify-content:center;padding:24px;background:var(--page);
color:var(--ink);text-align:center;-webkit-font-smoothing:antialiased;
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{width:100%;max-width:400px;background:var(--card);border:1px solid var(--border);border-radius:16px;
padding:36px 32px 32px}
.pair{display:flex;align-items:center;justify-content:center;gap:14px;height:64px;margin-bottom:22px}
.pair img{height:44px;display:block}.pair .pear{height:50px}
.plus{width:14px;height:14px;position:relative}
.plus:before,.plus:after{content:"";position:absolute;background:var(--faint);border-radius:1px}
.plus:before{left:6px;top:0;width:2px;height:14px}.plus:after{top:6px;left:0;width:14px;height:2px}
h1{margin:0;font-size:22px;line-height:1.3;font-weight:650;letter-spacing:-.015em}
p{margin:8px 0 0;color:var(--muted)}
.note{margin-top:14px;font-size:13px;color:var(--faint)}
.code{margin-top:14px;font:12px ui-monospace,Menlo,Consolas,monospace;color:var(--faint)}
</style></head><body><main>
<div class="pair">$logos</div>
<h1>$title</h1><p>$line</p>$extra
</main>$script</body></html>
""")
_PEAR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "icons", "mark@4x.png")


def _image(path: str, css: str, alt: str) -> str:
    try:
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return ""
    return f'<img class="{css}" src="data:image/png;base64,{data}" alt="{alt}">'


def _page(texts: tuple, ok: bool, partner_logo: str = "", error: str = "") -> str:


    title, line, note = (tuple(texts) + ("", ""))[:3]
    pear, partner = _image(_PEAR, "pear", "AI Agent"), _image(partner_logo, "", "QGIS") if partner_logo else ""
    extra = (f'<p class="note">{html.escape(note)}</p>' if note else "") + (
        f'<p class="code">Error: {html.escape(error)}</p>' if error and not ok else "")
    return _PAGE.substitute(
        logos=pear + ('<span class="plus"></span>' + partner if pear and partner else partner),
        title=html.escape(title), line=html.escape(line), extra=extra,

        script="<script>setTimeout(function(){window.close()},4000)</script>" if ok else "")


class Loopback:



    def __init__(self, done: tuple, failed: tuple, partner_logo: str = ""):

        self.state = secrets.token_urlsafe(24)
        self._pages = (done, failed)
        self._logo = partner_logo
        self._result: tuple[str, str] | None = None
        self.params: dict[str, str] = {}
        self._event = threading.Event()
        self._lock = threading.Lock()
        owner = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            timeout = 10

            def do_GET(self):  # noqa: N802
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if query.get("state", [""])[0] != owner.state:

                    self.send_response(404)
                    self.end_headers()
                    return
                code, error = query.get("code", [""])[0], query.get("error", [""])[0]
                owner.params = {k: v[0] for k, v in query.items() if k not in ("code", "state") and v}
                owner._finish(code, error or ("" if code else "no_code"))
                page = owner._pages[0 if code else 1]
                body = _page(page, bool(code), owner._logo, error or "").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.timeout = 0.5
        self.redirect_uri = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._serve, name="oauth-loopback", daemon=True)
        self._thread.start()

    def _finish(self, code: str, error: str) -> None:
        with self._lock:
            if self._result is None:
                self._result = (code, error)
        self._event.set()

    def _serve(self) -> None:
        try:
            while not self._event.is_set():
                self._server.handle_request()
        finally:
            self._server.server_close()

    def done(self) -> bool:
        return self._event.is_set()

    def result(self) -> tuple[str, str]:
        return self._result or ("", "cancelled")

    def close(self) -> None:

        self._finish("", "cancelled")


def _plain_open(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout)  # nosec B310


def _send(request, opener, timeout: float) -> tuple[int, dict]:
    try:
        response = (opener or _plain_open)(request, timeout)
        try:
            return response.status, _json(response.read(1 << 20))
        finally:
            response.close()
    except urllib.error.HTTPError as exc:
        return exc.code, _json(exc.read(1 << 20))


def post_form(url: str, fields: dict, opener=None, timeout: float = 20.0) -> tuple[int, dict]:


    data = urllib.parse.urlencode(fields).encode("ascii")
    return _send(urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}), opener, timeout)


def get_json(url: str, token: str, opener=None, timeout: float = 20.0, headers: dict | None = None) -> tuple[int, dict]:
    return _send(urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                                      "Accept": "application/json", **(headers or {})}),
                 opener, timeout)


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def exchange(token_uri: str, client_id: str, client_secret: str, code: str, verifier: str,
             redirect_uri: str, opener=None) -> tuple[int, dict]:
    return post_form(token_uri, {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                 "client_id": client_id, "client_secret": client_secret,
                                 "redirect_uri": redirect_uri}, opener)


def refresh(token_uri: str, client_id: str, client_secret: str, refresh_token: str,
            opener=None) -> tuple[int, dict]:
    return post_form(token_uri, {"grant_type": "refresh_token", "refresh_token": refresh_token,
                                 "client_id": client_id, "client_secret": client_secret}, opener)
