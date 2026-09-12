# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from .card_base import _UNBOUNDED_PX, _Card, plain_label
from .card_controls import (
    _ASK_CARD_QSS,
    _ASK_DONE_QSS,
    _ASK_LABEL_QSS,
    _ASK_MARGINS,
    _ASK_TEXT_QSS,
    _BTN_PRIMARY_PILL,
    _BTN_QUIET_LINK,
    _PROPOSAL_MAX_PX,
    FoldMixin,
    _pill,
    done_row,
)
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import icon_for, pixmap_for
from .style import ACCENT, ACCENT_INK, ACCENT_TINT, FONT_BODY, FONT_HINT, INK_2, INK_3, SPACE_OUTER, SPACE_TIGHT, qcolor

CLAMP_LINES = 2

_PILL_QSS = scale_qss_font_px(
    f"QWidget#memoryPill {{ background: {ACCENT_TINT}; border: none; border-radius: 9px; }}"
    f"QLabel {{ color: {ACCENT_INK}; font-size: {FONT_HINT}px; font-weight: 600;"
    " background: transparent; border: none; }"
)
_NOTE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)
_CHEVRON_QSS = "QToolButton { background: transparent; border: none; padding: 0; }"


class MemoryCard(_Card, FoldMixin):


    decided = pyqtSignal(str, bool)
    settings_requested = pyqtSignal()

    def __init__(self, key: str, text: str, replaces_text: str = "", parent=None):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.setObjectName("memoryCard")
        self.set_frame(_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_OUTER)
        self.setMaximumWidth(scale_px_length(_PROPOSAL_MAX_PX))
        self.key = str(key or "")
        self.text = " ".join(str(text or "").split())
        self.decision: bool | None = None
        self._open = False

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACE_TIGHT + 2)
        body.addWidget(self._build_head())
        body.addWidget(self._build_note())
        if replaces_text.strip():
            old = plain_label(self.tr("Replaces: {0}").format(" ".join(replaces_text.split())), self._body)
            old.setObjectName("memoryReplaces")
            old.setStyleSheet(_ASK_LABEL_QSS)
            old.setWordWrap(True)
            body.addWidget(old)
        body.addWidget(self._build_footer())
        self._col.addWidget(self._body)

        self._done = done_row(self, "check", qcolor(INK_3), self.tr("Added to memory"))
        link = QPushButton(self.tr("Settings"), self._done)
        link.setObjectName("memorySettingsLink")
        link.setStyleSheet(_BTN_QUIET_LINK)
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.clicked.connect(self.settings_requested.emit)
        done_lay = self._done.layout()
        done_lay.setStretch(done_lay.indexOf(self._done.line_label), 0)
        done_lay.addWidget(link, 0)
        done_lay.addStretch(1)
        self._done.hide()
        self._col.addWidget(self._done)



    def _build_head(self) -> QWidget:
        row = QWidget(self._body)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_OUTER)
        pill = QWidget(row)
        pill.setObjectName("memoryPill")
        pill.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pill.setStyleSheet(_PILL_QSS)
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(6, 1, 8, 1)
        pl.setSpacing(4)
        glyph = QLabel(pill)
        glyph.setPixmap(pixmap_for(pill, "sparkles", 11, qcolor(ACCENT)))
        pl.addWidget(glyph)
        pl.addWidget(QLabel(self.tr("Memory"), pill))
        lay.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
        title = plain_label(self.tr("Remember this for next time?"), row)
        title.setStyleSheet(_ASK_TEXT_QSS)

        title.setWordWrap(True)
        self._title = title
        lay.addWidget(title, 1)
        return row

    def _build_note(self) -> QWidget:
        row = QWidget(self._body)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_TIGHT)
        self._note = plain_label(self.text, row)
        self._note.setObjectName("memoryNote")
        self._note.setStyleSheet(_NOTE_QSS)
        self._note.setWordWrap(True)
        self._note.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._note.setContentsMargins(0, 0, 0, 0)
        self._note.setToolTip(self.text)
        lay.addWidget(self._note, 1)
        self._chevron = QToolButton(row)
        self._chevron.setObjectName("memoryExpand")
        self._chevron.setStyleSheet(_CHEVRON_QSS)
        self._chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chevron.setToolTip(self.tr("Show the whole note"))
        self._chevron.clicked.connect(self.toggle)
        lay.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignBottom)
        return row

    def _build_footer(self) -> QWidget:
        footer = QWidget(self._body)
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_OUTER)
        lay.addStretch(1)
        no = self._button(self.tr("No thanks"), _BTN_QUIET_LINK, lambda: self._decide(False))
        no.setObjectName("memoryDecline")
        lay.addWidget(no)
        add = _pill(self._button(self.tr("Add"), _BTN_PRIMARY_PILL, lambda: self._decide(True)))
        add.setObjectName("memoryAdd")
        lay.addWidget(add)
        return footer



    def _line_px(self) -> int:
        return self._note.fontMetrics().lineSpacing()

    def _needs_clamp(self) -> bool:
        width = max(40, self._note.width())
        rect = self._note.fontMetrics().boundingRect(0, 0, width, 100000, int(Qt.TextFlag.TextWordWrap), self.text)
        return rect.height() > CLAMP_LINES * self._line_px() + 2

    def _apply_clamp(self) -> None:
        clamp = self._needs_clamp()
        self._chevron.setVisible(clamp)
        if clamp and not self._open:
            self._note.setMinimumHeight(0)
            self._note.setMaximumHeight(CLAMP_LINES * self._line_px())
        else:


            self._note.setMaximumHeight(_UNBOUNDED_PX)
            self._note.setMinimumHeight(self._note.heightForWidth(max(40, self._note.width())))
        if self._title.width() > 0:
            self._title.setMinimumHeight(self._title.heightForWidth(self._title.width()))
        name = "chevron_up" if self._open else "chevron_down"
        self._chevron.setIcon(icon_for(self._chevron, name, 14, qcolor(INK_3)))
        self._chevron.setToolTip(self.tr("Show less") if self._open else self.tr("Show the whole note"))

    def toggle(self) -> None:
        self._open = not self._open
        self._apply_clamp()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_clamp()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._apply_clamp()



    def _decide(self, add: bool) -> None:
        if self.decision is not None:
            return
        self.decision = add
        self._body.setEnabled(False)
        self.decided.emit(self.key, add)

    def confirm(self) -> None:

        self.fold_body(self._body, self._show_done)

    def _show_done(self) -> None:
        self._body.hide()
        self._done.show()
        self.set_frame(_ASK_DONE_QSS)
        self.set_margins(0, 0, 0, 0)
        self.setMaximumWidth(_UNBOUNDED_PX)

    def to_markdown(self) -> str:
        return f"- **{self.tr('Memory')}:** {self.text}"
