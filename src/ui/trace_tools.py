# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The activity rows of a run's trace: one line per source the agent used."""
























from __future__ import annotations

import json

from qgis.PyQt.QtCore import QRectF, QSize, Qt
from qgis.PyQt.QtGui import QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from qgis.PyQt.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from .cards_tool import ToolCard
from .font_scale import scale_point_size, scale_px_length, widget_pixel_ratio
from .icons import pixmap_for
from .source_marks import source_host, source_mark_pixmap
from .style import FIELD, FONT_HINT, INK_2, INK_3, qcolor
from .tool_describe import call_subject, describe_tool_call, group_number, result_facts
from .trace_rows import (
    _HINT_QSS,
    _LABEL_QSS,
    _SECOND_QSS,
    GLYPH_PX,
    GLYPH_SLOT_PX,
    ROW_MIN_PX,
    Chevron,
    HoverRow,
    _row_layout,
)
from .widgets import FlowLayout, Spinner


CHIP_PX = 20
CHIP_MARK_PX = 14
_CHIP_MAX_W = 260
_CHIP_PAD_X = 7
_CHIP_GAP = 5

MAX_RESULTS = 3
_CHEVRON_PX = 12


SEARCH_TOOLS = frozenset({
    "search_open_data", "geocode", "reverse_geocode", "search_stac_items",
    "search_earthdata_collections", "search_earthdata_granules", "search_gee_catalog",
    "web_search", "qgis_docs", "search_plugin_repository", "list_stac_collections",
})
CODE_TOOLS = frozenset({"execute_code", "run_processing", "evaluate_expression", "run_code",
                        "run_python", "run_model"})

_LIST_KEYS = ("results", "items", "datasets", "collections", "granules", "matches",
              "sections", "plugins", "sources", "hits")
_NAME_KEYS = ("title", "name", "display_name", "label", "id")
_SOURCE_KEYS = ("provider", "source", "portal", "publisher", "domain", "host", "organisation",
                "organization", "collection", "url", "href", "link")


def is_search_tool(name: str, args=None) -> bool:
    """A call whose result is a list of sources worth showing as chips."""
    return str(name or "") in SEARCH_TOOLS


def is_code_tool(name: str) -> bool:
    return name in CODE_TOOLS


def query_of(name: str, args) -> str:
    """What was searched for, in words, never the raw request."""
    subject, _ = call_subject(name, args)
    return subject or describe_tool_call(name, args)


def search_results(detail: str) -> list[tuple[str, str]]:
    """``[(name, source), ...]`` from a tool's JSON detail, else nothing."""
    text = str(detail or "").strip()
    if not text or text[0] not in "[{":
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    items = data if isinstance(data, list) else None
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            if isinstance(data.get(key), list):
                items = data[key]
                break
    if not items:
        return []
    rows = []
    for item in items:
        if isinstance(item, str):
            rows.append((item.strip(), ""))
            continue
        if not isinstance(item, dict):
            continue
        props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        name = next((str(item.get(k) or props.get(k)) for k in _NAME_KEYS
                     if item.get(k) or props.get(k)), "")
        source = next((source_host(item.get(k) or props.get(k)) for k in _SOURCE_KEYS
                       if isinstance(item.get(k) or props.get(k), str)), "")
        if name.strip():
            rows.append((" ".join(name.split()), source))
    return rows


def layer_mark(name: str, size: int = CHIP_MARK_PX, ratio: float = 1.0) -> QPixmap | None:
    """The QGIS icon of the project layer called ``name``, or None."""
    if not name:
        return None
    try:
        from qgis.core import QgsProject

        from .layer_icons import layer_icon

        layers = QgsProject.instance().mapLayersByName(name)
        if not layers:
            return None
        icon = layer_icon(layers[0])
        if icon is None or icon.isNull():
            return None
        pixmap = icon.pixmap(QSize(int(size * ratio), int(size * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        return pixmap
    except Exception:  # noqa: BLE001 - outside QGIS, or a layer gone
        return None


def _same_subject(chip: str, subject: str) -> bool:
    """True when ``chip`` is ``subject`` again, whole or shortened: the describer trims a long query at one length and the subject at another, and."""


    chip = " ".join(chip.replace("...", "\u2026").split()).lower()
    subject = " ".join(subject.replace("...", "\u2026").split()).lower()
    if not chip or not subject:
        return False
    if chip == subject:
        return True
    head_a, head_b = chip.split("\u2026", 1)[0].strip(), subject.split("\u2026", 1)[0].strip()
    if head_a and head_b and (head_a.startswith(head_b) or head_b.startswith(head_a)):
        return True
    parts = [part.strip() for part in chip.split("\u2026")]
    return len(parts) > 1 and all(part in subject for part in parts if part)


class SubjectChip(QWidget):
    """One thing a call touched: a 20 px pill on the field, an optional 14 px mark, the text in the second ink at 11 px, elided at 260 px."""


    def __init__(self, text: str, mark: QPixmap | None = None, parent=None):
        super().__init__(parent)
        self._text = " ".join(str(text or "").split())
        self._mark = mark
        font = QFont(self.font())
        font.setPixelSize(scale_point_size(FONT_HINT))
        self.setFont(font)
        self.setFixedHeight(scale_px_length(CHIP_PX))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(self._text if len(self._text) > 24 else "")

    def text(self) -> str:
        return self._text

    def _text_left(self) -> int:
        return _CHIP_PAD_X + (CHIP_MARK_PX + _CHIP_GAP if self._mark is not None else 0)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override


        width = QFontMetrics(self.font()).horizontalAdvance(self._text) + 2
        return QSize(min(_CHIP_MAX_W, self._text_left() + width + _CHIP_PAD_X), CHIP_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), CHIP_PX / 2, CHIP_PX / 2)
            painter.fillPath(path, qcolor(FIELD))
            if self._mark is not None:
                y = (CHIP_PX - CHIP_MARK_PX) / 2
                painter.drawPixmap(QRectF(_CHIP_PAD_X, y, CHIP_MARK_PX, CHIP_MARK_PX), self._mark,
                                   QRectF(self._mark.rect()))
            left = self._text_left()
            painter.setFont(self.font())
            painter.setPen(qcolor(INK_2))


            text = QFontMetrics(self.font()).elidedText(
                self._text, Qt.TextElideMode.ElideRight, max(0, self.width() - left - _CHIP_PAD_X))
            painter.drawText(QRectF(left, 0, self.width() - left - _CHIP_PAD_X, self.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return


class ActivityRow(QWidget):
    """One source the agent used, its chips, and the calls behind it."""

    def __init__(self, card: ToolCard, parent=None):
        super().__init__(parent)
        self.cards: list[ToolCard] = []
        self.key = card.stack_key()
        self._open = False
        self._chips: list[QWidget] = []
        self._chip_texts: set = set()
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._head = HoverRow(self)
        row = _row_layout(self._head, ROW_MIN_PX)
        self._icon = QLabel(self._head)
        self._icon.setFixedSize(GLYPH_SLOT_PX, GLYPH_SLOT_PX)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._spinner = Spinner(GLYPH_PX, color=INK_3, parent=self._head)
        row.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignTop)


        self._flow_host = QWidget(self._head)



        policy = self._flow_host.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self._flow_host.setSizePolicy(policy)
        self._flow = FlowLayout(self._flow_host, _CHIP_GAP + 1, _CHIP_GAP)
        self._label = QLabel(self._flow_host)
        self._label.setObjectName("activityLabel")
        self._label.setStyleSheet(_LABEL_QSS)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setFixedHeight(scale_px_length(CHIP_PX))
        self._flow.addWidget(self._label)
        self._count = QLabel(self._flow_host)
        self._count.setObjectName("activityCount")
        self._count.setStyleSheet(_HINT_QSS)
        self._count.setFixedHeight(scale_px_length(CHIP_PX))
        self._count.hide()
        self._flow.addWidget(self._count)
        self._outcome = QLabel(self._flow_host)
        self._outcome.setObjectName("activityOutcome")
        self._outcome.setStyleSheet(_HINT_QSS)
        self._outcome.setFixedHeight(scale_px_length(CHIP_PX))
        self._outcome.hide()







        self._flow.addWidget(self._outcome)
        row.addWidget(self._flow_host, 1)
        self._chevron = Chevron(_CHEVRON_PX, self._head)
        self._chevron.hide()
        row.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignTop)
        self._head.hovered.connect(self._on_hover)
        self._head.clicked.connect(self.toggle)



        self._opens = is_code_tool(card.name)
        if not self._opens:
            self._head.setCursor(Qt.CursorShape.ArrowCursor)
        col.addWidget(self._head)

        self._body = QWidget(self)
        self._body_col = QVBoxLayout(self._body)
        self._body_col.setContentsMargins(0, 0, 0, 0)
        self._body_col.setSpacing(0)
        self._body.hide()
        col.addWidget(self._body)
        self.add(card)



    def add(self, card: ToolCard) -> None:
        """One more call to the same source: its chips join the line."""
        card.set_bare(True)
        card.setParent(self._body)
        card.hide()
        self._body_col.addWidget(card)
        self.cards.append(card)
        self._wrap(card)
        for text, mark in self._subjects(card):
            self._add_chip(text, mark)
        self.refresh()

    def _wrap(self, card: ToolCard) -> None:
        """The line follows the card: its end and its opening."""
        row = self
        original_finish, original_expand, original_repeat, original_unfinished = (
            card.finish, card.set_expanded, card.add_repeat, card.mark_unfinished)

        def finish(ok, summary, duration_s, detail="", _orig=original_finish):
            _orig(ok, summary, duration_s, detail)
            row.refresh()

        def set_expanded(expanded, _orig=original_expand, _card=card):
            _orig(expanded)
            _card.setVisible(bool(expanded))
            row._sync_open()

        def add_repeat(tool_call_id, _orig=original_repeat):
            _orig(tool_call_id)
            row.refresh()

        def mark_unfinished(_orig=original_unfinished):


            _orig()
            row.refresh()

        card.finish = finish
        card.set_expanded = set_expanded
        card.add_repeat = add_repeat
        card.mark_unfinished = mark_unfinished

    def _subjects(self, card: ToolCard) -> list:
        """``[(text, mark)]``: what the call touched, in words."""
        out = []
        ratio = widget_pixel_ratio(self)



        subject, _ = call_subject(card.name, card.args)
        if subject:
            out.append((subject, None))
        chip = str(card.chip_text() or "")
        if chip and not _same_subject(chip, subject):
            out.append((chip, layer_mark(chip, CHIP_MARK_PX, ratio)))
        return out

    def _add_chip(self, text: str, mark: QPixmap | None) -> None:
        key = " ".join(str(text or "").split()).lower()
        if not key or key in self._chip_texts:
            return
        self._chip_texts.add(key)
        chip = SubjectChip(text, mark, self._flow_host)

        self._flow.removeWidget(self._outcome)
        self._flow.addWidget(chip)
        self._flow.addWidget(self._outcome)
        self._chips.append(chip)

    def _clear_result_chips(self) -> None:
        for chip in [c for c in self._chips if getattr(c, "_is_result", False)]:
            self._chips.remove(chip)
            self._chip_texts.discard(chip.text().lower())
            self._flow.removeWidget(chip)
            chip.hide()
            chip.setParent(None)
            chip.deleteLater()

    @property
    def repeats(self) -> int:
        return sum(int(getattr(card, "repeats", 1) or 1) for card in self.cards)

    def label(self) -> str:
        return self._label.text()

    def line(self) -> str:
        """What the row says, for the block head: one call is the source and its subject (``Inspect insee.fr``); several are the source and the count."""


        label = self._label.text()
        n = self.repeats
        if n > 1:
            return f"{label} × {n}".strip()


        named = [c for c in self._chips if getattr(c, "_mark", None) is not None
                 and not getattr(c, "_is_result", False)]
        chips = [c.text() for c in (named or self._chips)[:1]]
        if label and any(c.lower().startswith(label.lower()) for c in chips):
            return " ".join(chips).strip()
        return " ".join([label] + chips).strip()



    def refresh(self) -> None:
        first = self.cards[0]
        last = self.cards[-1]
        running = any(card.ok is None for card in self.cards)
        self._spinner.setVisible(running)
        self._icon.setVisible(not running)
        if running:
            self._spinner.start()
        else:
            self._spinner.stop()
        connector = getattr(first, "connector", None) or {}
        label = str(connector.get("name") or "") or str(getattr(first, "_verb", "") or "")
        self._label.setText(label or describe_tool_call(first.name, first.args))
        failed = not running and last.ok is False
        self._label.setStyleSheet(_SECOND_QSS if failed else _LABEL_QSS)
        colour = first.glyph_colour() if callable(getattr(first, "glyph_colour", None)) \
            else qcolor(INK_2)
        if failed:
            self._icon.setPixmap(pixmap_for(self, "dash", GLYPH_PX, qcolor(INK_3)))
        else:
            self._icon.setPixmap(pixmap_for(self, str(first.glyph or "circle"), GLYPH_PX, colour))
        n = self.repeats
        self._count.setText(f"× {n}")
        self._count.setVisible(n > 1)
        self._sync_outcome(running, failed)
        line = ", ".join(card.line() for card in self.cards)
        self.setToolTip(line if len(line) > 40 else "")

    def _sync_outcome(self, running: bool, failed: bool) -> None:
        """What the last finished call came back with, after the chips."""
        self._clear_result_chips()
        text = ""
        if failed:
            text = self.tr("did not work")
        elif not running:
            last = self.cards[-1]
            if is_search_tool(last.name):
                rows = search_results(last.detail) if last.ok else []
                ratio = widget_pixel_ratio(self)
                for name, source in rows[:MAX_RESULTS]:
                    chip = SubjectChip(name, source_mark_pixmap(source or name, CHIP_MARK_PX, ratio),
                                       self._flow_host)
                    chip._is_result = True
                    self._flow.removeWidget(self._outcome)
                    self._flow.addWidget(chip)
                    self._flow.addWidget(self._outcome)
                    self._chips.append(chip)
                    self._chip_texts.add(chip.text().lower())
                if len(rows) > MAX_RESULTS:
                    text = f"+{len(rows) - MAX_RESULTS}"
            if not text:
                facts = result_facts(last.summary)
                if facts["count"] is not None:
                    count = int(facts["count"])
                    text = (self.tr("1 feature") if count == 1
                            else self.tr("{n} features").format(n=group_number(count)))
                elif facts["layer"] and facts["layer"].lower() not in self._chip_texts:
                    text = f"→ {facts['layer']}"
        self._outcome.setText(text)
        self._outcome.setVisible(bool(text))



    def _on_hover(self, hot: bool) -> None:
        self._chevron.set_hot(hot)
        self._chevron.setVisible(self._opens and (hot or self._open))

    def opens(self) -> bool:
        return self._opens

    def toggle(self) -> None:
        if self._opens:
            self.set_open(not self._open)

    def set_open(self, open_: bool) -> None:
        open_ = bool(open_) and self._opens
        for card in self.cards:
            card.set_expanded(open_)

    def _sync_open(self) -> None:
        self._open = any(card.is_expanded() for card in self.cards)
        self._body.setVisible(self._open)
        self._chevron.set_open(self._open, 0)
        self._chevron.setVisible(self._opens and (self._open or self._head.is_hot()))

    def is_open(self) -> bool:
        return self._open

    def cleanup(self) -> None:
        self._spinner.stop()
        self._chevron.cleanup()
        for card in self.cards:
            cleanup = getattr(card, "cleanup", None)
            if callable(cleanup):
                cleanup()

    def to_markdown(self) -> str:
        return "\n".join(card.to_markdown() for card in self.cards)


__all__ = ["CODE_TOOLS", "SEARCH_TOOLS", "ActivityRow", "SubjectChip", "is_code_tool",
           "is_search_tool", "layer_mark", "query_of", "search_results"]
