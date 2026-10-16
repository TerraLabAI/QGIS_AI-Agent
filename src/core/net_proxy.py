# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import os
import urllib.parse


def _proxy_url(found: dict | None) -> str | None:




    if not found or not found.get("host"):
        return None
    user = str(found.get("user") or "")
    password = str(found.get("password") or "")
    auth = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(password, safe='')}@" if user else ""
    port = found.get("port") or 0
    return f"http://{auth}{found['host']}{':' + str(port) if port else ''}"


_exported_no_proxy: str | None = None


def _share_exclusions(excluded: list[str]) -> None:






    global _exported_no_proxy
    current = os.environ.get("no_proxy")
    if current and current != _exported_no_proxy:
        return
    value = ",".join(excluded)
    if value:
        os.environ["no_proxy"] = value
        _exported_no_proxy = value
    elif current:
        del os.environ["no_proxy"]
        _exported_no_proxy = None


def apply_qgis_proxy() -> bool:





























    from . import net
    from .ws_proxy import qgis_exclusions, resolve_proxy

    try:
        found = resolve_proxy("example.com", 443, True)
        excluded = qgis_exclusions()
    except Exception:  # noqa: BLE001
        found, excluded = None, []
    _share_exclusions(excluded)
    net.set_proxy(_proxy_url(found))
    _apply_qgis_trust(net)
    return net.proxy_in_use() is not None


def _apply_qgis_trust(net) -> None:

    try:
        from . import missing_roots
        from .logger import log
        from .ws_proxy import resolve_tls



        missing_roots.ensure()



        ca_pem, _ = resolve_tls("")
        if net.set_trust(ca_pem):
            log("Tool fetches now also trust the certificates QGIS trusts.")
    except Exception as exc:  # noqa: BLE001
        try:
            from .logger import log_warning as _warn

            _warn(f"QGIS trusted certificates not read: {exc}")
        except Exception:  # nosec B110
            pass
