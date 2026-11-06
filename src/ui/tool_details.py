# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

import json
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..core.qt_compat import one_shot
from .card_base import humanise_tool_name
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .source_marks import _TextButton
from .style import FONT_HINT, GREEN, INK_2, INK_3, LINE_SOFT, MONO_FAMILY, RED, qcolor
from .tool_describe import _algorithm, is_processing_call, source_text
from .widgets import ChatLabel

_KEY_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; border: none; }}")
_VALUE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none; }}")
_MONO_QSS = scale_qss_font_px(
    f"QLabel {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px; color: {INK_3};"
    " background: transparent; border: none; }")
_VALUE_CHARS = 160
_FIRST_ROWS = 4
_COPIED_MS = 1500
_MARK_PX = 12


def _layers() -> dict:

    try:
        from qgis.core import QgsProject
        by_id = dict(QgsProject.instance().mapLayers())
    except Exception:  # noqa: BLE001
        return {}
    out = dict(by_id)
    names: dict = {}
    for layer in by_id.values():
        names.setdefault(layer.name(), []).append(layer)
    for name, found in names.items():
        if len(found) == 1:
            out.setdefault(name, found[0])
    return out


def _layer_text(layer) -> str:

    try:
        crs = layer.crs().authid()
    except Exception:  # noqa: BLE001
        crs = ""
    return f"{layer.name()} \u00b7 {crs}" if crs else layer.name()


def _distance_unit(definition, params: dict, layers: dict) -> str:


    try:
        if definition is None or definition.type() != "distance":
            return ""
        parent = layers.get(params.get(definition.parentParameterName()))
        if parent is None:
            return ""
        from qgis.core import QgsUnitTypes
        return QgsUnitTypes.toAbbreviatedString(parent.crs().mapUnits())
    except Exception:  # noqa: BLE001
        return ""


def _value_text(value, layers: dict) -> str:
    if isinstance(value, str):
        layer = layers.get(value)
        text = _layer_text(layer) if layer is not None else value
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif value is None:
        text = "null"
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        if isinstance(value, list):
            value = [layers[v].name() if isinstance(v, str) and v in layers else v for v in value]
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    return text


def _shown(text: str) -> str:

    path = text.startswith(("/", "~", "\\\\")) or (len(text) > 2 and text[1] == ":" and text[2] in "/\\")
    if len(text) > 40 and path and "\n" not in text:
        base = os.path.basename(text.replace("\\", "/").rstrip("/"))
        if base:
            return base
    return text


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def call_head(name: str, args) -> str:


    args = args if isinstance(args, dict) else {}
    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    if not (is_processing_call(name) and algorithm):
        return ""
    return algorithm


def call_fields(name: str, args) -> list:

    args = args if isinstance(args, dict) else {}
    layers = _layers()
    fields = []
    rest = args
    if is_processing_call(name) and isinstance(args.get("parameters"), dict):
        found = _algorithm(args)
        params = args["parameters"]
        for key, value in params.items():
            definition = found.parameterDefinition(str(key)) if found is not None else None
            label = definition.description() if definition is not None and definition.description() else str(key)
            text = _value_text(value, layers)
            unit = _distance_unit(definition, params, layers)
            fields.append((label, f"{text} {unit}" if unit else text, str(key)))
        rest = {k: v for k, v in args.items() if k not in ("parameters", "algorithm_id", "algorithm")}
    shown, _language = source_text(name, args)
    for key, value in rest.items():
        if shown and value == shown:
            continue
        fields.append((humanise_tool_name(str(key)), _value_text(value, layers), str(key)))
    return fields


def processing_python(name: str, args) -> str:

    args = args if isinstance(args, dict) else {}
    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    params = args.get("parameters")
    if not (is_processing_call(name) and algorithm and isinstance(params, dict)):
        return ""
    body = "{\n" + "".join(f"    {key!r}: {value!r},\n" for key, value in params.items()) + "}"
    return f"import processing\n\nprocessing.run({algorithm!r}, {body})\n"


def dialog_parameters(args) -> tuple[str, dict]:



    args = args if isinstance(args, dict) else {}
    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    params = args.get("parameters") if isinstance(args.get("parameters"), dict) else {}
    found = _algorithm(args)
    if found is None:
        return "", {}
    kept = {}
    for key, value in params.items():
        definition = found.parameterDefinition(str(key))
        if definition is not None and definition.isDestination():
            continue
        kept[key] = value
    return algorithm, kept


class ToolDetails(QWidget):


    def __init__(self, card, parent=None):
        super().__init__(parent)
        self._card = card
        self._all = False
        outer = QVBoxLayout(self)

        outer.setContentsMargins(13, 2, 0, 8)
        box = self._box = QFrame(self)
        box.setObjectName("toolDetails")
        box.setStyleSheet(f"QFrame#toolDetails {{ background: transparent; border: none;"
                          f" border-left: 2px solid {LINE_SOFT}; }}")
        outer.addWidget(box)
        col = QVBoxLayout(box)
        col.setContentsMargins(12, 2, 0, 2)
        col.setSpacing(4)
        algorithm = call_head(card.name, card.args)
        if algorithm:
            what = QLabel(algorithm, self)
            what.setStyleSheet(_MONO_QSS)
            what.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            col.addWidget(what)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(3)
        self._grid.setColumnStretch(1, 1)
        col.addLayout(self._grid)
        self._more = _TextButton("", "", self)
        self._more.clicked.connect(self._toggle_all)
        col.addWidget(self._more)
        result = QHBoxLayout()
        result.setContentsMargins(0, 2, 0, 0)
        result.setSpacing(6)
        self._mark = QLabel(self)
        self._mark.setFixedSize(_MARK_PX, _MARK_PX)
        result.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)
        self._result = ChatLabel("", self, wrap=True, selectable=True)
        self._result.setStyleSheet(_VALUE_QSS)
        result.addWidget(self._result, 1)
        col.addLayout(result)


        self._dialog = None
        self._copy = None
        algorithm, _params = dialog_parameters(card.args) if is_processing_call(card.name) else ("", {})
        if algorithm or processing_python(card.name, card.args):
            links = QHBoxLayout()
            links.setContentsMargins(0, 2, 0, 0)
            links.setSpacing(16)
            if algorithm:
                reopen = _TextButton(self.tr("Open in Processing"), "gear", self)
                reopen.setToolTip(self.tr("Open this algorithm's dialog with the same settings, to check them or run "
                                          "it again. The output goes to a new temporary layer."))
                reopen.clicked.connect(self._open_dialog)
                links.addWidget(reopen)
            if processing_python(card.name, card.args):
                self._copy = _TextButton(self.tr("Copy as Python"), "copy", self)
                self._copy.setToolTip(self.tr("The processing.run line, ready for the QGIS Python console"))
                self._copy.clicked.connect(self._copy_python)
                links.addWidget(self._copy)
            links.addStretch(1)
            col.addLayout(links)
        self._timer = one_shot(self, self._reset_copy)
        self.refresh()

    def _reset_copy(self) -> None:
        if self._copy is not None:
            self._copy.set_text(self.tr("Copy as Python"))
            self._copy.set_glyph("copy")

    def _open_dialog(self) -> None:
        algorithm, params = dialog_parameters(self._card.args)
        if not algorithm:
            return
        try:
            from processing import createAlgorithmDialog
            dialog = createAlgorithmDialog(algorithm, params)
        except Exception:  # noqa: BLE001
            dialog = None
        if dialog is None:
            return
        self._dialog = dialog
        dialog.show()
        dialog.raise_()

    def _toggle_all(self) -> None:
        self._all = not self._all
        self.refresh()

    def refresh(self) -> None:

        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:

                item.widget().hide()
                item.widget().deleteLater()
        card = self._card
        fields = call_fields(card.name, card.args)
        hidden = 0 if self._all else max(0, len(fields) - _FIRST_ROWS)
        width = 0
        for index, (label, value, _key) in enumerate(fields[:len(fields) - hidden]):
            name = QLabel(label, self._box)
            name.setStyleSheet(_KEY_QSS)
            name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            shown = _shown(value)
            text = ChatLabel(_clip(shown, _VALUE_CHARS), self._box, wrap=True, selectable=True)
            text.setStyleSheet(_VALUE_QSS)
            self._grid.addWidget(name, index, 0, Qt.AlignmentFlag.AlignTop)
            self._grid.addWidget(text, index, 1)
            name.ensurePolished()
            width = max(width, name.fontMetrics().horizontalAdvance(label) + 2)
        self._grid.setColumnMinimumWidth(0, min(width, 150))
        extra = max(0, len(fields) - _FIRST_ROWS)
        self._more.set_text(self.tr("Fewer") if self._all else self.tr("{n} more").format(n=extra))
        self._more.setVisible(extra > 0)
        words = []
        if card.ok is not None:

            if card.ok and (card.summary or "").strip():
                words.append(_clip(card.summary.strip(), 300))
        self._result.setText(" \u00b7 ".join(words))
        if card.ok is not None:
            self._mark.setPixmap(pixmap_for(self, "check" if card.ok else "close", _MARK_PX,
                                            qcolor(GREEN if card.ok else RED)))
        for part in (self._mark, self._result):
            part.setVisible(bool(words))

    def _copy_python(self) -> None:
        QApplication.clipboard().setText(processing_python(self._card.name, self._card.args))
        self._copy.set_text(self.tr("Copied"))
        self._copy.set_glyph("check")
        self._timer.start(_COPIED_MS)
