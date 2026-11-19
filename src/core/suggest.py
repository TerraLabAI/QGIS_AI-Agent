# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import time
import uuid

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.utils import iface

from . import protocol
from .qt_compat import one_shot

IGNORED_STEP = 3
BACK_OFF_AFTER = 9
BACK_OFF_S = 600.0
MAX_LAYERS = 40

FIELD_LAYERS, MAX_FIELDS = 8, 40
_DEFAULT_PAUSE_MS = 120
_KINDS = {QgsWkbTypes.GeometryType.PointGeometry: "point", QgsWkbTypes.GeometryType.LineGeometry: "line",
          QgsWkbTypes.GeometryType.PolygonGeometry: "polygon"}

_PICTURE_PROVIDERS = frozenset({"wms", "xyz", "arcgismapserver", "vectortile", "mbtilesvectortiles"})


def project_facts(active=None) -> dict:


    project = QgsProject.instance()
    nodes = [node for node in project.layerTreeRoot().findLayers() if node.layer() is not None]
    nodes.sort(key=lambda node: node.layer() is not active)
    out = []
    for node in nodes[:MAX_LAYERS]:
        layer = node.layer()
        if isinstance(layer, QgsVectorLayer):
            kind = _KINDS.get(layer.geometryType(), "table")
            selected = layer.selectedFeatureCount()
        elif isinstance(layer, QgsRasterLayer):
            provider = layer.dataProvider()
            kind = "other" if provider is not None and provider.name() in _PICTURE_PROVIDERS else "raster"
            selected = 0
        else:
            kind, selected = "other", 0
        row = {"name": layer.name()[:200] or layer.id()[:200], "kind": kind, "crs": layer.crs().authid()[:64]}
        if selected:
            row["selected"] = int(selected)
        row["source"] = layer.providerType()[:32]
        row["visible"] = bool(node.isVisible())
        if isinstance(layer, QgsVectorLayer) and len(out) < FIELD_LAYERS:
            row["fields"] = [field.name()[:64] for field in layer.fields()][:MAX_FIELDS]
        out.append(row)
    facts = {"project_crs": project.crs().authid()[:64], "layers": out}
    if active is not None:
        facts["active_layer"] = active.name()[:200]
    view = _view_wgs84(project)
    if view:
        facts["view"] = view
    return facts


def _view_wgs84(project) -> list:

    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None or not canvas.mapSettings().destinationCrs().isValid():
        return []
    to_wgs84 = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(),
                                      QgsCoordinateReferenceSystem("EPSG:4326"), project)
    try:
        box = to_wgs84.transformBoundingBox(canvas.extent())
    except QgsCsException:
        return []
    return [round(v, 4) for v in (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())]


class Suggester(QObject):




    ready = pyqtSignal(object, str)
    predicted = pyqtSignal(str, str, str)

    def __init__(self, session, active_layer=None, parent=None, enabled=None):
        super().__init__(parent)
        self._session = session
        self._enabled = enabled or (lambda: True)
        self._active_layer = active_layer or (lambda: None)
        self._request = ""
        self._prepared = ""
        self._asked_text = ""
        self._pending = ""
        self._thread = ""
        self._shown: list[str] = []
        self._ignored = 0
        self._paused_until = 0.0
        self._dismissed = False
        self._timer = one_shot(self, self._ask, _DEFAULT_PAUSE_MS)
        session.suggestions.connect(self._on_suggestions)
        session.next_prompt.connect(self._on_next_prompt)

    @property
    def enabled(self) -> bool:

        return bool(self._enabled())

    @property
    def available(self) -> bool:
        return self._session.suggest_terms is not None and self.enabled

    def _term(self, name: str, default: int) -> int:
        value = (self._session.suggest_terms or {}).get(name)
        try:
            return default if value is None else int(value)
        except (TypeError, ValueError):
            return default

    def typed(self, text: str, quiet: bool = False, thread_id: str = "") -> None:


        self._timer.stop()
        self._thread = thread_id
        self._request = ""
        raw, text = text, text.strip()
        if self._shown and not any(s.casefold().startswith(text.casefold()) for s in self._shown if text):

            self._shown = []
            self._ignored += 1
        if not text:
            self._dismissed = False
        if (quiet or self._dismissed or not self.available or "\n" in text
                or not self._term("min_chars", 3) <= len(text) <= self._term("max_chars", 160)
                or time.monotonic() < self._paused_until):
            self.ready.emit([], text)
            return
        back_off_after = self._term("back_off_after", BACK_OFF_AFTER)
        if 0 < back_off_after <= self._ignored:
            self._ignored = 0
            self._paused_until = time.monotonic() + BACK_OFF_S
            self.ready.emit([], text)
            return
        self._pending = text
        step = self._term("ignored_step", IGNORED_STEP)
        steps = min(self._ignored // step, 3) if step > 0 else 0

        pause = 0 if raw[-1:].isspace() else self._term("debounce_ms", _DEFAULT_PAUSE_MS)
        self._timer.setInterval(pause * (2 ** steps))
        self._timer.start()

    def _ask(self) -> None:
        if not self._pending or not self.available:
            return
        self._request = uuid.uuid4().hex[:16]
        self._asked_text = self._pending
        if not self._session.send_frame(protocol.suggest(self._request, self._pending,
                                                         project_facts(self._active_layer()), self._thread)):
            self._request = ""

    def prepare(self, thread_id: str = "") -> None:

        if not self.available or self._dismissed or time.monotonic() < self._paused_until:
            return
        self._prepared = uuid.uuid4().hex[:16]
        if not self._session.send_frame(protocol.suggest(self._prepared, "", project_facts(self._active_layer()),
                                                         thread_id)):
            self._prepared = ""

    def _on_suggestions(self, request_id: str, items, _code: str) -> None:
        if self._prepared and request_id == self._prepared:
            self._prepared = ""
            self.ready.emit(list(items or []), "")
            return
        if not self._request or request_id != self._request:
            return
        self._request = ""
        self._shown = [str(i.get("text") or "") for i in items or []]
        self.ready.emit(list(items or []), self._asked_text)

    def _on_next_prompt(self, thread_id: str, run_id: str, text: str) -> None:
        self.predicted.emit(thread_id, run_id, text)

    def taken(self) -> None:

        self._shown = []
        self._ignored = 0

    def dismissed(self) -> None:

        self._timer.stop()
        self._request = ""
        self._shown = []
        self._dismissed = True
        self._ignored += IGNORED_STEP

    def shutdown(self) -> None:
        self._timer.stop()
        try:
            self._session.suggestions.disconnect(self._on_suggestions)
            self._session.next_prompt.disconnect(self._on_next_prompt)
        except (TypeError, RuntimeError):
            pass
