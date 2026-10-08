# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






































from __future__ import annotations

import contextlib
import re
import threading
import urllib.parse

from . import security
from .logger import log_warning
from .serialization import coded_like

_REFUSALS_MAX = 8
_SOURCE_CHARS = 4000

_run_layers: set = set()
_refusals: dict = {}
_active_run = ""
_lock = threading.Lock()
_generation = 0


def _urls_in(layer) -> list[str]:

    try:
        source = urllib.parse.unquote(layer.source() or "")
    except Exception:  # noqa: BLE001
        return []
    found = []
    for match in security._URL_IN_TEXT_RE.finditer(source[:_SOURCE_CHARS]):
        head = re.match(r"(?i)^(https?)://([^/?#&]*)", match.group(0))
        if not head:
            continue
        netloc = security._TEMPLATE_LABEL_RE.sub("", head.group(2))
        url = f"{head.group(1).lower()}://{netloc}/"
        if netloc and url not in found:
            found.append(url)
    return found


def _user_layer_hosts() -> set:

    from qgis.core import QgsProject

    hosts = set()
    for layer_id, layer in QgsProject.instance().mapLayers().items():
        if layer_id in _run_layers:
            continue
        for url in _urls_in(layer):
            hosts.add(security.normal_host(url))
    return hosts


def checked(layer_id: str) -> bool:





    return str(layer_id) in _run_layers


def note_run_layers(layers) -> None:

    from qgis.core import QgsProject

    project = QgsProject.instance()
    _run_layers.intersection_update(project.mapLayers().keys())
    noted = []
    for layer in layers or ():
        with contextlib.suppress(Exception):
            noted.append((layer.id(), layer.name(), _urls_in(layer)))


    _run_layers.update(layer_id for layer_id, _name, _urls in noted)
    for layer_id, name, urls in noted:
        pending, refused = [], False
        for url in urls:
            host = security.normal_host(url)
            if not host or security._routed_public_host(host):
                continue
            name_to_ask = security.name_to_resolve(url)
            if name_to_ask and security.start_lookup(name_to_ask) is not None:
                pending.append(url)
            elif _judge(layer_id, name, url, _active_run, deferred=True):
                refused = True
                break
        if pending and not refused:
            _judge_later(layer_id, name, pending)


def _judge(layer_id: str, name: str, url: str, run_id: str, deferred: bool) -> bool:


    problem = security.validate_url(url)
    if not problem or security.normal_host(url) in _user_layer_hosts():
        return False
    _refuse(layer_id, name, problem, run_id, deferred)
    return True


def _judge_later(layer_id: str, name: str, urls: list) -> None:

    generation, run_id = _generation, _active_run

    def wait() -> None:
        for url in urls:
            security.resolve_host(security.name_to_resolve(url))

        def judge() -> None:
            if generation != _generation or not checked(layer_id):
                return
            for url in urls:
                if _judge(layer_id, name, url, run_id, deferred=False):
                    return

        from . import background

        background.main_thread_invoker().invoke(judge)

    try:
        threading.Thread(target=wait, name="ai-agent-layer-hosts", daemon=True).start()
    except RuntimeError:
        log_warning(f"Layer address check skipped for '{name}': no thread to look its host up.")


def _refuse(layer_id: str, name: str, problem: str, run_id: str, deferred: bool) -> None:

    sentence = coded_like(f"The web layer '{name}' a tool call added was removed before QGIS drew it: {problem} "
                          "Ask the user if they meant it.", problem)
    with _lock:
        if run_id:
            queue = _refusals.setdefault(run_id, [])
            if sentence not in queue:
                queue.append(sentence)
                del queue[:-_REFUSALS_MAX]
    log_warning(sentence)

    def remove() -> None:
        from ..tools.processing_run import remove_layers

        _run_layers.discard(layer_id)
        remove_layers([layer_id])

    if deferred:


        from qgis.PyQt.QtCore import QTimer

        QTimer.singleShot(0, remove)
    else:
        remove()


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


def uninstall() -> None:

    global _generation, _active_run
    _generation += 1
    _active_run = ""
    with _lock:
        _refusals.clear()
    _run_layers.clear()
