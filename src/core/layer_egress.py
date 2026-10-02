# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















































from __future__ import annotations

import threading
import time
import urllib.parse

from . import security
from .logger import log_warning
from .net_hosts import OWN_HOSTS

_BLOCKED_URL = "blocked-by-ai-agent://local-network"
_DECISION_S = 30.0
_UNRESOLVED_S = 10.0
_VERDICTS_MAX = 512
_REFUSALS_MAX = 8


_run_layer_ids: frozenset = frozenset()
_run_layer_hosts: frozenset = frozenset()
_user_layer_hosts: frozenset = frozenset()
_paired: tuple = ("", None)
_layer_hosts: dict = {}
_verdicts: dict = {}
_lookups: set = set()
_noted: set = set()
_refusals: dict = {}
_refused_logged: dict = {}
_active_run = ""
_lock = threading.Lock()
_preprocessor_id = ""
_connected = None


def _hosts_in(layer) -> frozenset:
    try:
        source = urllib.parse.unquote(layer.source() or "")
    except Exception:  # noqa: BLE001
        return frozenset()
    hosts = set()
    for match in security._URL_IN_TEXT_RE.finditer(source[:4000]):
        host = security.normal_host(match.group(0))
        if host:
            hosts.add(host)
    return frozenset(hosts)


def _paired_server() -> tuple:
    try:
        from .settings import Settings

        parts = urllib.parse.urlsplit(str(Settings().server_url or ""))
        return ((parts.hostname or "").strip("[]").lower().rstrip("."), parts.port)
    except Exception:  # noqa: BLE001
        return ("", None)


def _publish() -> None:
    global _run_layer_ids, _run_layer_hosts, _user_layer_hosts, _paired
    run_ids, run_hosts, user = set(), set(), set()
    for layer_id, (hosts, by_run) in _layer_hosts.items():
        if by_run:
            if hosts:
                run_ids.add(layer_id)
                run_hosts |= hosts
        else:
            user |= hosts
    _run_layer_ids, _run_layer_hosts, _user_layer_hosts = frozenset(run_ids), frozenset(run_hosts), frozenset(user)
    _paired = _paired_server()


def _on_layers_added(layers) -> None:
    for layer in layers or ():
        try:
            _layer_hosts[layer.id()] = (_hosts_in(layer), False)
        except Exception:  # nosec B110
            pass
    _publish()


def _on_layers_removed(layer_ids) -> None:
    for layer_id in layer_ids or ():
        _layer_hosts.pop(str(layer_id), None)
    _publish()


def note_run_layers(layers) -> None:





    changed = False
    names: set = set()
    for layer in layers or ():
        try:
            hosts = _hosts_in(layer)
            _layer_hosts[layer.id()] = (hosts, True)
            names |= hosts
            changed = True
        except Exception:  # nosec B110
            pass
    if changed:
        _publish()
        for host in names:
            _look_up(host)


def begin_run(run_id: str) -> None:

    global _active_run
    with _lock:
        _refusals.pop(run_id, None)
    _active_run = str(run_id or "")


def end_run(run_id: str) -> None:

    global _active_run
    with _lock:
        _refusals.pop(run_id, None)
    if _active_run == run_id:
        _active_run = ""


def take_refusals(run_id: str) -> list[str]:

    with _lock:
        return _refusals.pop(run_id, None) or []


def _tied(host: str) -> bool:



    hosts = _run_layer_hosts
    return host in hosts or any(host.endswith("." + known) for known in hosts)


def _is_name(host: str) -> bool:
    return security._as_address(host) is None


def _look_up(host: str) -> None:

    if not host or not _is_name(host):
        return
    with _lock:
        if host in _lookups:
            return
        _lookups.add(host)
    try:
        threading.Thread(target=_lookup_worker, args=(host,), name="ai-agent-egress-dns", daemon=True).start()
    except RuntimeError:
        with _lock:
            _lookups.discard(host)


def _lookup_worker(host: str) -> None:
    refused, resolved = False, False
    try:
        addresses = security.resolve_host(host)
        for text in addresses:
            address = security._as_address(text)
            if address is None:
                continue
            resolved = True
            if security._address_is_local(address) or security._address_is_private(address):
                refused = True
    except Exception:  # noqa: BLE001
        resolved = False
    ttl = _DECISION_S if resolved else _UNRESOLVED_S
    with _lock:
        if len(_verdicts) >= _VERDICTS_MAX:
            _verdicts.clear()
        _verdicts[host] = (time.monotonic() + ttl, refused, resolved)
        _lookups.discard(host)
        unresolved = not resolved and host not in _noted
        if unresolved:
            _noted.add(host)
    if unresolved:
        log_warning(f"Layer address check: {host} did not resolve, so its requests for a web layer a tool call "
                    "added are not checked against local addresses.")


def _note_unverified(host: str) -> None:

    with _lock:
        if host in _noted:
            return
        if len(_noted) >= _VERDICTS_MAX:
            _noted.clear()
        _noted.add(host)
    log_warning(f"Layer address check: the first request to {host} for a web layer a tool call added went out "
                "before its address was known (no lookup runs on a network thread); later ones are checked.")


def _judge(url: str, host: str) -> bool:


    if host in security._user_hosts or host in _user_layer_hosts:
        return False
    if host in OWN_HOSTS:
        return False
    paired_host, paired_port = _paired
    if paired_host and host == paired_host:
        try:
            if urllib.parse.urlsplit(url).port == paired_port:
                return False
        except ValueError:
            pass

    if security.is_local_url(url, resolve=False) or security.is_private_url(url, resolve=False):
        return True
    if not _is_name(host):
        return False
    now = time.monotonic()
    with _lock:
        entry = _verdicts.get(host)
    if entry is None:
        _look_up(host)
        _note_unverified(host)
        return False
    if entry[0] <= now:
        _look_up(host)
    return entry[1]


def _refuse(host: str) -> None:
    sentence = (f"QGIS was about to fetch {host} for a web layer a tool call added; that address is on "
                "this computer or the local network and the user did not write it, so the request "
                "was not sent. Ask the user if they meant it.")
    now = time.monotonic()
    with _lock:
        run_id = _active_run
        if run_id:
            queue = _refusals.setdefault(run_id, [])
            if sentence not in queue:
                queue.append(sentence)
                del queue[:-_REFUSALS_MAX]
        say = _refused_logged.get(host, -_DECISION_S) + _DECISION_S <= now
        if say:
            if len(_refused_logged) >= _VERDICTS_MAX:
                _refused_logged.clear()
            _refused_logged[host] = now
    if say:
        log_warning(sentence)


def _preprocess(request) -> None:

    try:
        if not _run_layer_hosts:
            return
        url = request.url()
        if url.scheme().lower() not in ("http", "https"):
            return
        text = url.toString()
        host = security.normal_host(text)
        if not host or not _tied(host):
            return
        if _judge(text, host):
            from qgis.PyQt.QtCore import QUrl

            _refuse(host)
            request.setUrl(QUrl(_BLOCKED_URL))
            return


        from qgis.PyQt.QtNetwork import QNetworkRequest

        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
    except Exception:  # nosec B110
        pass


def install() -> None:

    global _preprocessor_id, _connected
    if _preprocessor_id:
        return
    try:
        from qgis.core import Qgis, QgsNetworkAccessManager, QgsProject

        from .data_access import _PREPROCESSOR_CRASHES

        version = int(Qgis.QGIS_VERSION_INT)
        if _PREPROCESSOR_CRASHES[0] <= version < _PREPROCESSOR_CRASHES[1]:

            log_warning(f"Layer address check is off on QGIS {Qgis.QGIS_VERSION}: its request preprocessor "
                        "crashes there, so web layers a tool call adds fetch unchecked (the add tools "
                        "still check the address they are given).")
            return
        project = QgsProject.instance()
        _layer_hosts.clear()
        for layer in project.mapLayers().values():
            _layer_hosts[layer.id()] = (_hosts_in(layer), False)
        _publish()
        project.layersAdded.connect(_on_layers_added)
        project.layersRemoved.connect(_on_layers_removed)
        _connected = project
        _preprocessor_id = QgsNetworkAccessManager.setRequestPreprocessor(_preprocess) or ""
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layer address check not installed: {exc}")


def uninstall() -> None:

    global _preprocessor_id, _connected, _active_run
    project, _connected = _connected, None
    if project is not None:
        for signal, slot in ((project.layersAdded, _on_layers_added),
                             (project.layersRemoved, _on_layers_removed)):
            try:
                signal.disconnect(slot)
            except Exception:  # nosec B110
                pass
    try:
        from qgis.core import QgsNetworkAccessManager

        if _preprocessor_id:
            QgsNetworkAccessManager.removeRequestPreprocessor(_preprocessor_id)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layer address check not removed: {exc}")
    _preprocessor_id = ""
    _active_run = ""
    with _lock:
        _verdicts.clear()
        _noted.clear()
        _refusals.clear()
        _refused_logged.clear()
    _layer_hosts.clear()
    _publish()
