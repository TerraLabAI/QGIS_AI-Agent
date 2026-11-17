# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

from qgis.core import QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QLocale, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from ..core.qt_compat import disconnect_quietly, qgis_iface
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .style import FONT_HINT, INK_2, INK_3, SPACE_TIGHT
from .widgets import ElidedLabel, IconButton



MAX_FROZEN_IDS = 50_000
_GLYPH = "lu.square-dashed-mouse-pointer"
_GLYPH_PX = 14
_TEXT_QSS = scale_qss_font_px(f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none;")
_CLOSE_QSS = ("QToolButton { border: none; background: transparent; padding: 0; }"
              "QToolButton:hover { background: transparent; }")


def selection_words(layer: str, count: int) -> str:

    if count == 1:
        return QCoreApplication.translate("SelectionLine", "{layer} · 1 feature selected").format(layer=layer)
    return QCoreApplication.translate("SelectionLine", "{layer} · {n} features selected").format(
        layer=layer, n=QLocale().toString(int(count)))


def selection_key(snapshot: dict | None):

    if not snapshot or snapshot.get("omit"):
        return None
    ids = snapshot.get("ids")
    return (snapshot.get("layer_id"), tuple(ids) if ids is not None else int(snapshot.get("count") or 0))


def _glyph_label(parent) -> QLabel:
    glyph = QLabel(parent)
    glyph.setFixedSize(scale_px_length(_GLYPH_PX), scale_px_length(_GLYPH_PX))
    glyph.setPixmap(pixmap_for(glyph, _GLYPH, _GLYPH_PX, QColor(INK_3)))
    return glyph


def sent_selection_row(parent, shown: dict) -> QWidget:

    row = QWidget(parent)
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 2, 0)
    line.setSpacing(SPACE_TIGHT + 2)
    line.addStretch(1)
    line.addWidget(_glyph_label(row), 0, Qt.AlignmentFlag.AlignVCenter)
    words = ElidedLabel(selection_words(str(shown.get("layer") or ""), int(shown.get("count") or 0)), row)
    words.setStyleSheet(_TEXT_QSS)

    words.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    line.addWidget(words, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class SelectionLine(QWidget):



    left_out = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer = None

        self._left_out = None
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 0, 0)
        row.setSpacing(SPACE_TIGHT + 2)
        self._glyph = _glyph_label(self)
        row.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self._text = ElidedLabel("", self)
        self._text.setStyleSheet(_TEXT_QSS)
        row.addWidget(self._text, 1, Qt.AlignmentFlag.AlignVCenter)
        self._close = IconButton(self, None, 12, self.tr("Leave the selection out of this message"), _CLOSE_QSS)
        self._close.setFixedSize(scale_px_length(18), scale_px_length(18))
        self._close.clicked.connect(self._leave_out)


        self._close.installEventFilter(self)
        self._show_close(False)
        row.addWidget(self._close, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setToolTip(self.tr("The AI agent sees this selection with your message, along with the map"
                                " view and the project's layers."))
        self.hide()
        iface = qgis_iface()
        if iface is not None:
            iface.currentLayerChanged.connect(self._follow)
            self._follow(iface.activeLayer())

    def unwire(self) -> None:

        disconnect_quietly(qgis_iface(), "currentLayerChanged", self._follow)
        self._follow(None)

    def _follow(self, layer) -> None:
        if self._layer is not None:
            for signal in ("selectionChanged", "nameChanged"):
                disconnect_quietly(self._layer, signal, self.refresh)
            disconnect_quietly(self._layer, "willBeDeleted", self._on_layer_gone)
        self._layer = layer if isinstance(layer, QgsVectorLayer) else None
        if self._layer is not None:
            self._layer.selectionChanged.connect(self.refresh)
            self._layer.nameChanged.connect(self.refresh)
            self._layer.willBeDeleted.connect(self._on_layer_gone)
        self.refresh()

    def _on_layer_gone(self) -> None:
        self._layer = None
        self.refresh()

    def _count(self) -> int:
        try:
            return int(self._layer.selectedFeatureCount()) if self._layer is not None else 0
        except RuntimeError:
            self._layer = None
            return 0

    def refresh(self, *_args) -> None:
        count = self._count()
        key = (self._layer.id(), count) if count else None
        if self._left_out is not None and self._left_out != key:
            self._left_out = None
        if not count or self._left_out is not None:
            self.hide()
            return
        words = selection_words(self._layer.name(), count)
        self._text.setText(words)
        self.setAccessibleName(words)
        self.show()

    def _leave_out(self) -> None:
        count = self._count()
        if count:
            self._left_out = (self._layer.id(), count)
        keyboard = self._close.hasFocus()
        self.hide()
        if keyboard:
            self.left_out.emit()

    def _show_close(self, shown: bool) -> None:
        self._close.set_icon("lu.x", 12, QColor(INK_3) if shown else QColor(0, 0, 0, 0))

    def snapshot(self) -> dict | None:





        count = self._count()
        if not count:
            return None
        layer = self._layer
        ids = sorted(int(i) for i in layer.selectedFeatureIds()) if count <= MAX_FROZEN_IDS else None
        return {"layer_id": layer.id(), "layer_name": layer.name(), "count": count, "ids": ids,
                "omit": self._left_out == (layer.id(), count)}

    def sent(self) -> None:

        self._left_out = None
        self.refresh()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._close and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._show_close(event.type() == QEvent.Type.FocusIn or self.underMouse())
        return False

    def enterEvent(self, event):  # noqa: N802
        self._show_close(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._show_close(self._close.hasFocus())
        super().leaveEvent(event)

