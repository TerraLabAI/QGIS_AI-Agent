# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




























from __future__ import annotations

import json
import os
import re

from qgis.PyQt.QtCore import QCoreApplication, Qt
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
from ..tools.harvest_processing import dialog_call
from .card_base import humanise_tool_name
from .console_script import console_script
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .qgis_links import qgis_link
from .source_marks import _TextButton
from .style import (
    FIELD,
    FONT_BODY,
    FONT_HINT,
    GREEN,
    INK,
    INK_2,
    INK_3,
    INSET,
    LINE,
    LINE_SOFT,
    MONO_FAMILY,
    RADIUS_CHIP,
    RADIUS_CONTROL,
    RED,
    TAGS,
    qcolor,
)
from .tool_describe import (
    _algorithm,
    _plain_note,
    check_warning,
    count_text,
    group_number,
    is_processing_call,
    result_facts,
    source_text,
)
from .widgets import ChatLabel, FlowLayout

_KEY_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; border: none; }}")
_VALUE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK}; background: transparent; border: none; }}")
_RESULT_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK}; background: transparent; border: none; }}")



_CARD_QSS = (f"QFrame#toolDetails {{ background: {INSET}; border: 1px solid {LINE};"
             f" border-radius: {RADIUS_CONTROL}px; }}")
_CHIP_QSS = scale_qss_font_px(
    f"QLabel#toolAlgorithm {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px; color: {INK_2};"
    f" background: {FIELD}; border: 1px solid {LINE_SOFT}; border-radius: {RADIUS_CHIP}px;"
    " padding: 1px 6px; }")
_PAD_PX = 12
_VALUE_CHARS = 160
_FIRST_ROWS = 3
_CHOSEN_ROWS = 5
_SWATCH_PX = 12
_KEY_MAX_PX = 108
_COPIED_MS = 1500
_MARK_PX = 14


def _rule(parent) -> QFrame:
    line = QFrame(parent)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {LINE_SOFT}; border: none;")
    return line


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
        text = QCoreApplication.translate("ToolDetails", "Yes") if value \
            else QCoreApplication.translate("ToolDetails", "No")
    elif value is None:
        text = "null"
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        box = _box_text(value)
        if box:
            return box
        if isinstance(value, list):
            value = [layers[v].name() if isinstance(v, str) and v in layers else v for v in value]
            if value and all(isinstance(v, str) for v in value):
                return ", ".join(value)
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    return text


_BOX_KEYS = ("west", "south", "east", "north")
_SUMMARY_KEY = re.compile(r"[a-z_]+\s*:")


_HANDLE_KEY = re.compile(r"(?:^|_)(?:index|id|token)$")
_DEGREE_KEYS = frozenset({"heading", "pitch", "sun_azimuth", "sun_altitude", "rotation", "azimuth"})
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=['\"A-Z0-9])")


def _box_text(value) -> str:


    if not (isinstance(value, dict) and set(value) == set(_BOX_KEYS)):
        return ""
    try:
        return " \u00b7 ".join(f"{key[0].upper()} {round(float(value[key]), 5):g}" for key in _BOX_KEYS)
    except (TypeError, ValueError):
        return ""


def warning_words(card) -> tuple[str, str]:



    whole = ""
    try:
        data = json.loads(card.detail or "")
        checks = data.get("checks") if isinstance(data, dict) else None
        found = checks.get("warnings") if isinstance(checks, dict) else None
        if isinstance(found, list) and found:
            whole = str(found[0])
        elif isinstance(data, dict) and isinstance(data.get("verified"), dict):
            whole = str(data["verified"].get("warning") or "")
    except (ValueError, AttributeError):
        whole = ""
    whole = " ".join(whole.split()) or check_warning(card.summary)
    return (_SENTENCE_END.split(whole, 1)[0], whole) if whole else ("", "")


def _picture_size(detail) -> str:

    try:
        data = json.loads(detail or "")
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    width, height = data.get("width"), data.get("height")
    if not all(isinstance(side, int) and not isinstance(side, bool) and side > 0 for side in (width, height)):
        return ""
    return f"{group_number(width)} \u00d7 {group_number(height)} px"


def result_words(card) -> str:


    summary = (card.summary or "").strip()
    facts = result_facts(summary)
    parts = [part for part in (facts["layer"], count_text(summary, card.detail)) if part]
    if parts:
        return " \u00b7 ".join(parts)
    size = _picture_size(card.detail)
    if size:
        return size

    if facts["facts"] and _SUMMARY_KEY.match(summary):
        return _plain_note(summary)
    return "" if not _plain_note(summary) else _clip(summary, 300)


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

        return dialog_call(name, args)[0]
    return algorithm


def _enum_text(definition, value) -> str:


    try:
        if definition is None or definition.type() != "enum":
            return ""
        options = list(definition.options())
        picks = value if isinstance(value, (list, tuple)) else [value]
        words = []
        for pick in picks:
            if isinstance(pick, bool) or not isinstance(pick, int) or not 0 <= pick < len(options):
                return ""
            words.append(str(options[pick]))
        return ", ".join(words)
    except Exception:  # noqa: BLE001
        return ""


def _is_default(definition, value) -> bool:

    try:
        if definition is None or definition.isDestination():
            return False
        default = definition.defaultValue()
    except Exception:  # noqa: BLE001
        return False
    if default is None or isinstance(value, (dict, list)):
        return False
    if isinstance(value, bool) or isinstance(default, bool):
        return isinstance(value, bool) and isinstance(default, bool) and value == default
    try:
        return float(value) == float(default)
    except (TypeError, ValueError):
        return str(value) == str(default)


_HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?")


def value_mark(value, layers: dict):



    if not isinstance(value, str):
        return None
    if value in layers:
        return layers[value]
    return value if _HEX_COLOUR.fullmatch(value.strip()) else None


def call_rows(name: str, args) -> list:



    args = args if isinstance(args, dict) else {}
    layers = _layers()
    rows = []
    rest = args
    if is_processing_call(name) and isinstance(args.get("parameters"), dict):
        found = _algorithm(args)
        params = args["parameters"]
        for key, value in params.items():
            definition = found.parameterDefinition(str(key)) if found is not None else None
            label = definition.description() if definition is not None and definition.description() else str(key)
            text = _enum_text(definition, value) or _value_text(value, layers)
            unit = _distance_unit(definition, params, layers)
            rows.append((label, f"{text} {unit}" if unit else text, str(key), _is_default(definition, value),
                         value_mark(value, layers)))
        rest = {k: v for k, v in args.items() if k not in ("parameters", "algorithm_id", "algorithm")}
    shown, _language = source_text(name, args)
    for key, value in rest.items():
        if shown and value == shown:
            continue
        if _HANDLE_KEY.search(str(key)) and not (isinstance(value, str) and value in layers):
            continue
        if key in _DEGREE_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
            rows.append((humanise_tool_name(str(key)), f"{value:g}\u00b0", str(key), False, None))
            continue
        rows.append((humanise_tool_name(str(key)), _value_text(value, layers), str(key), False,
                     value_mark(value, layers)))
    return [row for row in rows if not row[3]] + [row for row in rows if row[3]]


def asked_rows(name: str, args) -> list:


    return [row[:3] for row in call_rows(name, args) if any(c.isalpha() for c in row[1])]


def call_fields(name: str, args) -> list:

    return [row[:3] for row in call_rows(name, args)]


def _by_id(value, layers: dict):

    layer = layers.get(value) if isinstance(value, str) else None
    return layer.id() if layer is not None else value


def wrapped_dialog(name: str, args) -> tuple[str, dict]:


    algorithm, params = dialog_call(name, args)
    if not algorithm:
        return "", {}
    try:
        from qgis.core import QgsApplication
        if QgsApplication.processingRegistry().algorithmById(algorithm) is None:
            return "", {}
    except Exception:  # noqa: BLE001
        return "", {}
    layers = _layers()
    return algorithm, {key: _by_id(value, layers) for key, value in params.items() if value is not None}


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


        self.has_content = True
        outer = QVBoxLayout(self)

        outer.setContentsMargins(0, 2, 0, 8)
        box = self._box = QFrame(self)
        box.setObjectName("toolDetails")
        box.setStyleSheet(_CARD_QSS)
        outer.addWidget(box)
        col = QVBoxLayout(box)
        col.setContentsMargins(_PAD_PX, _PAD_PX - 2, _PAD_PX, _PAD_PX - 2)
        col.setSpacing(8)

        self._result_host = QWidget(box)
        result = QHBoxLayout(self._result_host)
        result.setContentsMargins(0, 0, 0, 0)
        result.setSpacing(8)
        self._mark = QLabel(self._result_host)
        self._mark.setFixedSize(_MARK_PX, _MARK_PX)
        self._mark.setContentsMargins(0, 0, 0, 0)
        result.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)
        self._result = ChatLabel("", self._result_host, wrap=True, selectable=True)
        self._result.setStyleSheet(_RESULT_QSS)
        self._result.setIndent(0)
        result.addWidget(self._result, 1)
        col.addWidget(self._result_host)
        self._result_rule = _rule(box)
        col.addWidget(self._result_rule)
        algorithm = call_head(card.name, card.args)
        self._what = None
        if algorithm:
            what = self._what = QLabel(algorithm, box)
            what.setObjectName("toolAlgorithm")
            what.setStyleSheet(_CHIP_QSS)
            what.setToolTip(self.tr("The algorithm's id, to find it in the Processing toolbox"))
            what.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            col.addWidget(what, 0, Qt.AlignmentFlag.AlignLeft)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(5)
        self._grid.setColumnStretch(1, 1)
        col.addLayout(self._grid)
        self._more = _TextButton("", "", box)
        self._more.clicked.connect(self._toggle_all)
        col.addWidget(self._more)


        self._dialog = None
        self._copy = None
        algorithm, _params = self._dialog_call()

        place = None if algorithm else qgis_link(card.name, card.args, card.detail)
        python = is_processing_call(card.name) and console_script(card.name, card.args)
        self._has_links = bool(algorithm or place or python)
        self._links_rule = None
        if self._has_links:
            self._links_rule = _rule(box)
            col.addWidget(self._links_rule)


            links_host = QWidget(box)
            links = FlowLayout(links_host, 16, 4)
            if algorithm:
                reopen = _TextButton(self.tr("Open in Processing"), "gear", box)
                reopen.setToolTip(self.tr("Open this algorithm's dialog with the same settings, to check them or run "
                                          "it again. The output goes to a new temporary layer."))
                reopen.clicked.connect(self._open_dialog)
                links.addWidget(reopen)
            if python:
                self._copy = _TextButton(self.tr("Copy as Python"), "copy", box)
                self._copy.setToolTip(self.tr("The processing.run line, ready for the QGIS Python console"))
                self._copy.clicked.connect(self._copy_python)
                links.addWidget(self._copy)
            if place:
                words, glyph, tip, open_place = place
                there = _TextButton(words, glyph, box)
                there.setToolTip(tip)
                there.clicked.connect(open_place)
                links.addWidget(there)
            col.addWidget(links_host)
        self._timer = one_shot(self, self._reset_copy)
        self.refresh()

    def _reset_copy(self) -> None:
        if self._copy is not None:
            self._copy.set_text(self.tr("Copy as Python"))
            self._copy.set_glyph("copy")

    def _dialog_call(self) -> tuple[str, dict]:
        card = self._card
        if is_processing_call(card.name):
            return dialog_parameters(card.args)
        return wrapped_dialog(card.name, card.args)

    def _open_dialog(self) -> None:
        algorithm, params = self._dialog_call()
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

    def _fields(self) -> list:

        card = self._card
        said = set()
        for part in ("subject_text", "chip_text", "source_text"):
            read = getattr(card, part, None)
            text = str(read() or "").strip() if callable(read) else ""
            if text:
                said.add(text)


        if self._listed():
            return []
        fields = []
        for label, value, key, default, mark in call_rows(card.name, card.args):
            if value.strip() in said:
                continue
            fields.append((label, value, key, default, mark))
        return fields

    @staticmethod
    def _first(fields: list) -> int:


        chosen = sum(1 for row in fields if not row[3])
        if 0 < chosen < len(fields):
            return min(chosen, _CHOSEN_ROWS)
        return min(len(fields), _FIRST_ROWS)

    def _with_mark(self, mark, text) -> QWidget:

        lead = None
        if isinstance(mark, str):
            lead = QLabel(self._box)
            lead.setFixedSize(_SWATCH_PX, _SWATCH_PX)
            lead.setStyleSheet(f"background: {mark.strip()}; border: 1px solid {LINE}; border-radius: 3px;")
        elif mark is not None:
            try:
                from qgis.core import QgsIconUtils
                pixmap = QgsIconUtils.iconForLayer(mark).pixmap(_MARK_PX, _MARK_PX)
            except Exception:  # noqa: BLE001
                pixmap = None
            if pixmap is not None and not pixmap.isNull():
                lead = QLabel(self._box)
                lead.setFixedSize(_MARK_PX, _MARK_PX)
                lead.setPixmap(pixmap)
        if lead is None:
            return text
        cell = QWidget(self._box)
        row = QHBoxLayout(cell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(lead, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(text, 1)
        return cell

    def _listed(self) -> bool:


        from .trace_tools import is_search_tool, search_results

        card = self._card
        if card.name == "fetch_text":

            return bool(card.ok)
        return is_search_tool(card.name) and bool(search_results(card.detail))

    def refresh(self) -> None:

        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:

                item.widget().hide()
                item.widget().deleteLater()
        card = self._card
        fields = self._fields()
        first = self._first(fields)
        hidden = 0 if self._all else len(fields) - first
        names = []
        for index, (label, value, key, _default, mark) in enumerate(fields[:len(fields) - hidden]):
            name = QLabel(label, self._box)
            name.setStyleSheet(_KEY_QSS)
            name.setIndent(0)
            name.setToolTip(key)
            name.setWordWrap(True)
            name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            names.append(name)
            shown = _shown(value)
            text = ChatLabel(_clip(shown, _VALUE_CHARS), self._box, wrap=True, selectable=True)
            text.setStyleSheet(_VALUE_QSS)
            text.setIndent(0)
            if shown != value:
                text.setToolTip(value)
            self._grid.addWidget(name, index, 0, Qt.AlignmentFlag.AlignTop)
            self._grid.addWidget(self._with_mark(mark, text), index, 1)
        if names:





            names[0].ensurePolished()
            metrics = names[0].fontMetrics()
            width = min(max(metrics.horizontalAdvance(row[0]) + 2 for row in fields), _KEY_MAX_PX)
            for name in names:
                name.setFixedWidth(width)
        extra = len(fields) - first
        self._more.set_text(self.tr("Fewer") if self._all else self.tr("{n} more").format(n=extra))
        self._more.setVisible(extra > 0)


        words, whole = warning_words(card) if card.ok else ("", "")
        warned = bool(words)
        if card.ok and not warned and not self._listed():
            words = result_words(card)
        self._result.setText(words)
        self._result.setToolTip(whole if whole != words else "")
        if warned:
            self._mark.setPixmap(pixmap_for(self, "warning", _MARK_PX, qcolor(TAGS["alert"][1])))
        elif card.ok is not None:
            self._mark.setPixmap(pixmap_for(self, "check" if card.ok else "close", _MARK_PX,
                                            qcolor(GREEN if card.ok else RED)))
        self._result_host.setVisible(bool(words))
        below = bool(fields) or self._what is not None
        self._result_rule.setVisible(bool(words) and below)
        if self._links_rule is not None:
            self._links_rule.setVisible(bool(words) or below)

        self.has_content = bool(words) or below or self._has_links
        self._box.setVisible(self.has_content)

    def _copy_python(self) -> None:
        QApplication.clipboard().setText(console_script(self._card.name, self._card.args))
        self._copy.set_text(self.tr("Copied"))
        self._copy.set_glyph("check")
        self._timer.start(_COPIED_MS)
