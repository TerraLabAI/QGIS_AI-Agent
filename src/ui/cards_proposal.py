# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The cards a run proposes with: a recommendation, and a table of edits."""













from __future__ import annotations

import re
import zlib

from qgis.PyQt.QtCore import QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.PyQt.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .card_base import _Card, plain_label
from .card_controls import (
    _ASK_CARD_QSS,
    _ASK_DONE_QSS,
    _ASK_LABEL_QSS,
    _ASK_MARGINS,
    _ASK_TEXT_QSS,
    _BTN_GHOST_PILL,
    _BTN_PRIMARY_PILL,
    _PROPOSAL_MAX_PX,
    AnswerFooter,
    FoldMixin,
    _Mark,
    _pill,
    ask_head,
    done_row,
)
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .shared import event_pos, exec_menu
from .style import (
    _MENU_QSS,
    CHIP_PX,
    FIELD,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    GREEN,
    GREEN_TINT,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_SOFT,
    LINE_STRONG,
    ORANGE,
    RADIUS_CHIP,
    RED,
    RED_TINT,
    SPACE_CARD,
    SPACE_OUTER,
    qcolor,
)
from .widgets import ElidedLabel, FlowLayout


_UNBOUNDED_PX = 16777215



_PROPOSAL_WORD_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)

_ENTITY_CHIP_QSS = (
    "QWidget#{name} { background: " + FIELD + "; border: 1px solid " + LINE_STRONG + ";"
    f" border-radius: {RADIUS_CHIP}px; }}"
    "QLabel { background: transparent; border: none; }"
)
_ENTITY_NAME_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none; }"
)

_CONFIDENCE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; font-weight: 500; color: {INK_2};"
    " background: transparent; border: none; }"
)

_ALTERNATIVES_QSS = scale_qss_font_px(
    _MENU_QSS + "QMenu::item { min-height: 32px; padding: 0 10px; }"
)


_CONFIDENCE = {
    "high": (3, GREEN),
    "medium": (2, ORANGE),
    "low": (1, RED),
}
_METER_BARS = 3
_METER_BAR_W = 3
_METER_BAR_H = 10
_METER_GAP = 2



_ENTITY_MARK_PX = 16
_ENTITY_INITIAL_PT = 8


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")



_DIFF_ROW_PX = 44
_DIFF_HEADER_PX = 38
_DIFF_SIDE_PX = 14
_DIFF_GAP_PX = 12
_DIFF_KINDS = ("added", "removed", "unchanged")


def _alpha(token: str, alpha: float) -> str:
    """``token`` as an ``rgba(...)`` at ``alpha``, for a border a shade lighter than its text (the value pill's green at 30 percent)."""

    colour = qcolor(token)
    try:
        return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, {alpha:.2f})"
    except (AttributeError, TypeError, ValueError):
        return token


def _hashed_colour(name: str) -> QColor:
    """A hue for ``name``, the same every time: crc32 keeps it stable across sessions where ``hash()`` would not."""

    hue = zlib.crc32((name or "").encode("utf-8")) % 360
    return QColor.fromHslF(hue / 360.0, 0.55, 0.52)


class _EntityMark(QWidget):
    """The 16 px round mark of an entity chip: its initial on a hashed colour."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self._initial = (name or "?").strip()[:1].upper() or "?"
        self._colour = _hashed_colour(name)
        self.setFixedSize(_ENTITY_MARK_PX, _ENTITY_MARK_PX)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._colour)
        painter.drawEllipse(QRectF(self.rect()))
        font = painter.font()
        font.setPointSize(_ENTITY_INITIAL_PT)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(Qt.GlobalColor.white))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._initial)


class _Meter(QWidget):
    """The confidence meter: three 3 by 10 bars, the filled ones in the confidence's colour, the rest in the strong hairline."""


    def __init__(self, filled: int, colour: str, parent=None):
        super().__init__(parent)
        self._filled = max(0, min(_METER_BARS, int(filled)))
        self._colour = colour
        self.setFixedSize(_METER_BARS * _METER_BAR_W + (_METER_BARS - 1) * _METER_GAP, _METER_BAR_H)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(_METER_BARS):
            painter.setBrush(qcolor(self._colour if i < self._filled else LINE_STRONG))
            x = i * (_METER_BAR_W + _METER_GAP)
            painter.drawRoundedRect(QRectF(x, 0, _METER_BAR_W, _METER_BAR_H), 1.5, 1.5)


def _entity_chip(parent: QWidget, name: str) -> QWidget:
    """The entity as a chip: the mark, the name, on the field ground."""
    chip = QWidget(parent)
    chip.setObjectName("entityChip")
    chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    chip.setStyleSheet(_ENTITY_CHIP_QSS.replace("{name}", chip.objectName()))
    chip.setFixedHeight(scale_px_length(CHIP_PX))
    lay = QHBoxLayout(chip)
    lay.setContentsMargins(3, 0, 6, 0)
    lay.setSpacing(SPACE_CARD - 2)
    lay.addWidget(_EntityMark(name, chip), 0, Qt.AlignmentFlag.AlignVCenter)
    label = plain_label(name, chip)
    label.setStyleSheet(_ENTITY_NAME_QSS)
    lay.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
    return chip


def _value_pill(parent: QWidget, text: str) -> QLabel:
    """The value as a pill: green on the green tint, a green hairline at 30 percent."""
    pill = plain_label(text, parent)
    pill.setStyleSheet(scale_qss_font_px(
        f"QLabel {{ background: {GREEN_TINT}; color: {GREEN};"
        f" border: 1px solid {_alpha(GREEN, 0.30)}; border-radius: {RADIUS_CHIP}px;"
        f" padding: 0 6px; font-size: {FONT_BODY}px; font-weight: 500; }}"))
    pill.setFixedHeight(scale_px_length(CHIP_PX - 2))
    return pill


class RecommendationCard(_Card, FoldMixin):
    """A run's proposal before a heavy step: the question, the proposal with its chips, the confidence meter, Alternatives and Accept."""








    decided = pyqtSignal(str, str)

    def __init__(self, tool_call_id: str, run_id: str, title: str, proposal: str,
                 entity: str = "", value: str = "", confidence: str = "high",
                 alternatives=None, parent=None):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_OUTER)
        self.setMaximumWidth(scale_px_length(_PROPOSAL_MAX_PX))
        self.tool_call_id = tool_call_id
        self.run_id = run_id
        self.title = (title or "").strip() or self.tr("Want me to run this?")
        self.proposal = " ".join((proposal or "").split())
        self.entity = (entity or "").strip()
        self.value = (value or "").strip()
        self.confidence = str(confidence or "high").lower()
        if self.confidence not in _CONFIDENCE:
            self.confidence = "high"
        if isinstance(alternatives, (str, bytes)) or not hasattr(alternatives, "__iter__"):
            alternatives = []
        self.alternatives = [str(a) for a in (alternatives or []) if str(a).strip()]
        self.decision: str | None = None

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACE_OUTER)
        body.addWidget(ask_head(self._body, self.title, lambda: self._decide("dismiss"),
                                self.tr("Dismiss"), dismiss_focusable=True))
        body.addWidget(self._build_proposal())
        body.addWidget(self._build_footer())
        self._col.addWidget(self._body)

        self._decision_row = done_row(self, "check", qcolor(INK_3), "")
        self._decision_row.hide()
        self._col.addWidget(self._decision_row)



    def _build_proposal(self) -> QWidget:
        """The sentence as words, with the chips where the placeholders are."""
        host = QWidget(self._body)
        flow = FlowLayout(host, 4, 4)
        text = self.proposal
        if self.entity and "{entity}" not in text:
            text = f"{text} {{entity}}".strip()
        if self.value and "{value}" not in text:
            text = f"{text} {{value}}".strip()
        for part in re.split(r"(\{entity\}|\{value\}|\s+)", text):
            if not part or part.isspace():
                continue
            if part == "{entity}":
                if self.entity:
                    flow.addWidget(_entity_chip(host, self.entity))
            elif part == "{value}":
                if self.value:
                    flow.addWidget(_value_pill(host, self.value))
            else:
                word = plain_label(part, host)
                word.setStyleSheet(_PROPOSAL_WORD_QSS)
                flow.addWidget(word)
        return host

    def _build_footer(self) -> QWidget:






        footer = AnswerFooter(self._body)
        filled, colour = _CONFIDENCE[self.confidence]
        left = QWidget(footer)
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(SPACE_OUTER)
        left_row.addWidget(_Meter(filled, colour, left), 0, Qt.AlignmentFlag.AlignVCenter)
        word = ElidedLabel(self.confidence_text(), left)
        word.setStyleSheet(_CONFIDENCE_QSS)
        left_row.addWidget(word, 1, Qt.AlignmentFlag.AlignVCenter)
        answers = []
        if self.alternatives:
            self._alternatives_btn = _pill(self._button(self.tr("Alternatives"), _BTN_GHOST_PILL,
                                                        self._show_alternatives))
            answers.append(self._alternatives_btn)
        else:
            self._alternatives_btn = None
        answers.append(_pill(self._button(self.tr("Accept"), _BTN_PRIMARY_PILL,
                                          lambda: self._decide("accept"))))
        footer.set_widgets(left, answers)
        return footer

    def confidence_text(self) -> str:
        return {
            "high": self.tr("High confidence"),
            "medium": self.tr("Medium confidence"),
            "low": self.tr("Low confidence"),
        }[self.confidence]



    def _show_alternatives(self) -> None:
        """A small popover under the button, one row per alternative."""
        button = self._alternatives_btn
        if button is None or self.decision is not None:
            return
        menu = QMenu(button)
        menu.setStyleSheet(_ALTERNATIVES_QSS)
        for text in self.alternatives:

            action = menu.addAction(text.replace("&", "&&"))
            action.triggered.connect(lambda checked=False, t=text: self._decide(f"alternative:{t}"))
        exec_menu(menu, button.mapToGlobal(button.rect().bottomLeft()))

    def _decide(self, decision: str) -> None:
        if self.decision is not None:
            return
        self.collapse(decision)
        self.decided.emit(self.tool_call_id, decision)

    def decision_text(self) -> str:
        decision = self.decision or ""
        if decision == "accept":
            return self.tr("Accepted")
        if decision.startswith("alternative:"):
            return f"{self.tr('Alternative')}: {decision[len('alternative:'):]}"
        return self.tr("Dismissed")

    def collapse(self, decision: str) -> None:
        """Fold the card shut, then one line stating the decision."""
        self.decision = decision
        dismissed = decision == "dismiss"
        self._decision_row.icon_label.setPixmap(
            pixmap_for(self, "dash" if dismissed else "check", 11, qcolor(INK_3)))
        self._decision_row.line_label.setText(f"{self.decision_text()} · {self.title}")
        self._body.setEnabled(False)
        self.fold_body(self._body, self._show_decision)

    def _show_decision(self) -> None:
        self._body.hide()
        self._decision_row.show()
        self.set_frame(_ASK_DONE_QSS)
        self.set_margins(0, 0, 0, 0)
        self.setMaximumWidth(_UNBOUNDED_PX)

    def to_markdown(self) -> str:
        decision = self.decision_text() if self.decision else self.tr("pending")
        return f"- **{self.tr('Proposal')}:** {self.title} ({decision})"






_DIFF_HEAD_QSS = (
    "QWidget#{name} { background: transparent; border: none;"
    " border-bottom: 1px solid " + LINE + "; }"
    "QLabel { background: transparent; border: none; }"
)
_DIFF_HINT_QSS = _ASK_LABEL_QSS


_DIFF_HEADER_BACK_QSS = (
    "QWidget#{name} { background: " + HOVER_ON + "; border: none;"
    " border-bottom: 1px solid " + LINE + "; }"
)
_DIFF_HEADER_CELL_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; font-weight: 500; color: {INK_2};"
    " background: transparent; border: none; }"
)
_DIFF_ROW_BACK_QSS = (
    "QWidget#{name} { background: {tint}; border: none;"
    " border-bottom: 1px solid " + LINE_SOFT + "; }"
)
_DIFF_FOOTER_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none; }}"
)

_DIFF_PILL_QSS = (
    "QWidget#{name} { background: " + FIELD + "; border: 1px solid " + LINE_STRONG + ";"
    f" border-radius: {RADIUS_CHIP}px; }}"
    "QLabel { background: transparent; border: none; }"
)
_DIFF_PILL_TEXT_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; font-weight: 500; color: {INK_2};"
    " background: transparent; border: none; }"
)
_DIFF_DOT_PX = 6

_WHITE = "white"


def _cell_qss(colour: str, first: bool, struck: bool) -> str:
    """The text of a cell: the first column 13 px medium, the rest 12 px; in the row's colour, struck when the change is a removal."""

    size = FONT_BASE if first else FONT_BODY
    weight = 500 if first else 400
    strike = " text-decoration: line-through;" if struck else ""
    return scale_qss_font_px(
        f"QLabel {{ font-size: {size}px; font-weight: {weight}; color: {colour};{strike}"
        " background: transparent; border: none; }"
    )


def _pill_cell(parent: QWidget, text: str, colour: str) -> QWidget:
    pill = QWidget(parent)
    pill.setObjectName("diffPill")
    pill.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)



    pill.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    pill.setStyleSheet(_DIFF_PILL_QSS.replace("{name}", pill.objectName()))
    pill.setFixedHeight(scale_px_length(CHIP_PX))
    lay = QHBoxLayout(pill)
    lay.setContentsMargins(8, 0, 8, 0)
    lay.setSpacing(SPACE_CARD)
    dot = QLabel(pill)
    dot.setFixedSize(_DIFF_DOT_PX, _DIFF_DOT_PX)
    dot.setStyleSheet(f"QLabel {{ background: {colour}; border: none; border-radius: {_DIFF_DOT_PX // 2}px; }}")
    lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
    label = plain_label(text, pill)
    label.setStyleSheet(_DIFF_PILL_TEXT_QSS)
    lay.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
    return pill


class _DiffRow:
    """One row of the table: its background, its cells, its check, and whether the change it proposes still applies."""


    _serial = 0

    def __init__(self, table: QWidget, grid: QGridLayout, index: int, row: dict, ncols: int):
        _DiffRow._serial += 1
        self.id = str(row.get("id") or index)
        kind = str(row.get("kind") or "unchanged").lower()
        self.kind = kind if kind in _DIFF_KINDS else "unchanged"
        self.changed = self.kind != "unchanged"
        self.active = True
        cells = row.get("cells")
        cells = list(cells) if isinstance(cells, (list, tuple)) else []
        cells = (cells + [""] * ncols)[:ncols]
        try:
            struck_at = int(row.get("changed", ncols - 1))
        except (TypeError, ValueError):
            struck_at = ncols - 1

        self.back = QWidget(table)
        self.back.setObjectName(f"diffRow{_DiffRow._serial}")
        self.back.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.back.setFixedHeight(scale_px_length(_DIFF_ROW_PX))
        grid.addWidget(self.back, index, 0, 1, ncols + 1)
        self._labels: list = []
        for col, cell in enumerate(cells):
            first = col == 0
            if isinstance(cell, dict) and cell.get("pill") is not None:
                colour = str(cell.get("colour") or "")
                widget = _pill_cell(table, str(cell.get("pill")), colour if _HEX_RE.match(colour) else INK_3)
                grid.addWidget(widget, index, col, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                continue




            label = ElidedLabel(str(cell), table)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            grid.addWidget(label, index, col, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._labels.append((label, first, self.kind == "removed" and col == struck_at))
        self.mark = None
        if self.changed:
            self.mark = _Mark("check", table, colour=self._colour(), glyph=_WHITE)
            self.mark.set_checked(True)
            grid.addWidget(self.mark, index, ncols, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._paint()

    def _colour(self) -> str:
        return {"added": GREEN, "removed": RED}.get(self.kind, INK)

    def _paint(self) -> None:
        on = self.active and self.changed
        tint = {"added": GREEN_TINT, "removed": RED_TINT}.get(self.kind, "transparent") if on else "transparent"
        self.back.setStyleSheet(_DIFF_ROW_BACK_QSS.replace("{name}", self.back.objectName())
                                .replace("{tint}", tint))
        for label, first, struck in self._labels:
            colour = self._colour() if on else (INK if first else INK_2)
            if self.changed and not on:
                colour = INK_3
            label.setStyleSheet(_cell_qss(colour, first, struck and on))


            font = label.font()
            font.setStrikeOut(bool(struck and on))
            label.setFont(font)
        if self.mark is not None:
            self.mark.set_checked(on)

    def toggle(self) -> None:
        if not self.changed:
            return
        self.active = not self.active
        self._paint()


class _DiffBody(QWidget):
    """The table's grid: a click on a changed row toggles it."""






    toggled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list = []
        self.setMouseTracking(True)

    def _row_at(self, pos):
        for row in self.rows:
            if row.changed and row.back.geometry().contains(pos):
                return row
        return None

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt override
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._row_at(event_pos(event))
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            row = self._row_at(event_pos(event))
            if row is not None:
                row.toggle()
                self.toggled.emit()
        super().mouseReleaseEvent(event)


class DiffTableCard(_Card, FoldMixin):
    """The attribute edits a run proposes, row by row, with Apply."""









    applied = pyqtSignal(str, object)

    def __init__(self, run_id: str, title: str, columns=None, rows=None, parent=None):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(0, 0, 0, 0)
        self._col.setSpacing(0)
        self.setMaximumWidth(scale_px_length(_PROPOSAL_MAX_PX))
        self.run_id = run_id
        self.title = (title or "").strip() or self.tr("Proposed changes")
        if isinstance(columns, (str, bytes)) or not hasattr(columns, "__iter__"):
            columns = []
        self.columns = [str(c) for c in (columns or [])]
        if isinstance(rows, (str, bytes)) or not hasattr(rows, "__iter__"):
            rows = []
        self._rows_data = [r for r in (rows or []) if isinstance(r, dict)]
        self.applied_ids: list | None = None

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_head())
        self._table = self._build_table()
        body.addWidget(self._table)
        body.addWidget(self._build_footer())
        self._col.addWidget(self._body)

        self._decision_row = done_row(self, "check", qcolor(INK_3), "")
        self._decision_row.hide()
        self._col.addWidget(self._decision_row)
        self._refresh()



    def _build_head(self) -> QWidget:
        head = QWidget(self._body)
        head.setObjectName("diffHead")
        head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head.setStyleSheet(_DIFF_HEAD_QSS.replace("{name}", head.objectName()))
        head.setFixedHeight(scale_px_length(_DIFF_ROW_PX))
        lay = QHBoxLayout(head)
        lay.setContentsMargins(_DIFF_SIDE_PX, 0, _DIFF_SIDE_PX, 0)
        lay.setSpacing(SPACE_OUTER)


        title = ElidedLabel(self.title, head)
        title.setStyleSheet(_ASK_TEXT_QSS)
        lay.addWidget(title, 1, Qt.AlignmentFlag.AlignVCenter)
        hint = ElidedLabel(self.tr("Click changed rows to toggle"), head)
        hint.setStyleSheet(_DIFF_HINT_QSS)
        hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hint.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        lay.addWidget(hint, 0, Qt.AlignmentFlag.AlignVCenter)
        return head

    def _build_table(self) -> _DiffBody:
        table = _DiffBody(self._body)
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(_DIFF_GAP_PX)
        grid.setVerticalSpacing(0)
        ncols = max(1, len(self.columns))

        back = QWidget(table)
        back.setObjectName("diffHeader")
        back.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        back.setStyleSheet(_DIFF_HEADER_BACK_QSS.replace("{name}", back.objectName()))
        back.setFixedHeight(scale_px_length(_DIFF_HEADER_PX))
        grid.addWidget(back, 0, 0, 1, ncols + 1)
        headers = []
        for col in range(ncols):



            label = ElidedLabel(self.columns[col] if col < len(self.columns) else "", table)
            label.setStyleSheet(_DIFF_HEADER_CELL_QSS)
            grid.addWidget(label, 0, col, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            headers.append(label)
        for index, row in enumerate(self._rows_data, start=1):
            table.rows.append(_DiffRow(table, grid, index, row, ncols))


        grid.setColumnMinimumWidth(ncols, scale_px_length(_DIFF_SIDE_PX) + 16)
        grid.setColumnStretch(ncols - 1, 1)




        if headers:
            headers[0].setContentsMargins(_DIFF_SIDE_PX, 0, 0, 0)
        for diff_row in table.rows:
            for label, _first, _struck in diff_row._labels[:1]:
                label.setContentsMargins(_DIFF_SIDE_PX, 0, 0, 0)
        table.toggled.connect(self._refresh)
        return table

    def _build_footer(self) -> QWidget:





        footer = AnswerFooter(self._body)
        footer.setContentsMargins(_DIFF_SIDE_PX, 0, _DIFF_SIDE_PX, 0)
        self._count_label = ElidedLabel("", footer)
        self._count_label.setStyleSheet(_DIFF_FOOTER_QSS)
        self._count_label.setMinimumHeight(scale_px_length(_DIFF_ROW_PX))
        self._apply_btn = _pill(self._button("", _BTN_PRIMARY_PILL, self._apply))
        footer.set_widgets(self._count_label, [self._apply_btn])
        return footer



    def selected_ids(self) -> list:
        return [row.id for row in self._table.rows if row.changed and row.active]

    def _counts(self) -> tuple:
        removed = sum(1 for row in self._table.rows if row.active and row.kind == "removed")
        added = sum(1 for row in self._table.rows if row.active and row.kind == "added")
        return removed, added

    def _refresh(self) -> None:
        removed, added = self._counts()
        parts = []
        if removed:
            parts.append(self.tr("{n} removal").format(n=removed) if removed == 1
                         else self.tr("{n} removals").format(n=removed))
        if added:
            parts.append(self.tr("{n} addition").format(n=added) if added == 1
                         else self.tr("{n} additions").format(n=added))
        self._count_label.setText(" · ".join(parts) if parts else self.tr("No change selected"))
        total = removed + added
        self._apply_btn.setText(self.tr("Apply {n} change").format(n=total) if total == 1
                                else self.tr("Apply {n} changes").format(n=total))
        self._apply_btn.setEnabled(total > 0)



    def _apply(self) -> None:
        if self.applied_ids is not None:
            return
        ids = self.selected_ids()
        self.collapse(ids)
        self.applied.emit(self.run_id, ids)

    def collapse(self, ids=None) -> None:
        """Fold the table shut, then one line saying what was applied."""
        self.applied_ids = list(ids or [])
        count = len(self.applied_ids)
        text = (self.tr("Applied {n} change").format(n=count) if count == 1
                else self.tr("Applied {n} changes").format(n=count))
        self._decision_row.line_label.setText(f"{text} · {self.title}")
        self._body.setEnabled(False)
        self.fold_body(self._body, self._show_decision)

    def _show_decision(self) -> None:
        self._body.hide()
        self._decision_row.show()
        self.set_frame(_ASK_DONE_QSS)
        self.set_margins(0, 0, 0, 0)
        self.setMaximumWidth(_UNBOUNDED_PX)

    def to_markdown(self) -> str:
        removed, added = self._counts()
        state = (self.tr("applied {n}").format(n=len(self.applied_ids))
                 if self.applied_ids is not None else self.tr("pending"))
        return f"- **{self.tr('Proposed changes')}:** {self.title}, +{added} -{removed} ({state})"
