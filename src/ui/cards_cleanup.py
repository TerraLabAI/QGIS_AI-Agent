# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The card that offers the run's working layers back."""

















from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .card_base import _UNBOUNDED_PX, _Card, plain_label
from .card_controls import (
    _ASK_CARD_QSS,
    _ASK_DONE_QSS,
    _ASK_MARGINS,
    _BTN_GHOST_PILL,
    _BTN_PRIMARY_PILL,
    FoldMixin,
    _pill,
    ask_head,
    done_row,
)
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .style import FONT_BODY, INK_2, INK_3, SPACE_OUTER, SPACE_TIGHT, qcolor




NAMES_SHOWN = 5

_CARD_MAX_PX = 420

_NAMES_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)


class CleanupCard(_Card, FoldMixin):
    """What the run left behind, and the two ways to take it back."""








    decided = pyqtSignal(str, str, list)

    def __init__(self, run_id: str, items, parent=None):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_OUTER)
        self.setMaximumWidth(scale_px_length(_CARD_MAX_PX))
        self.run_id = str(run_id or "")
        self.items = [dict(i) for i in (items or []) if isinstance(i, dict)]
        self.decision: str | None = None
        count = len(self.items)
        self.title = (self.tr("I left {n} working layer behind.").format(n=count) if count == 1
                      else self.tr("I left {n} working layers behind.").format(n=count))

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACE_OUTER)
        body.addWidget(ask_head(self._body, self.title, lambda: self._decide("keep"),
                                self.tr("Keep them"), dismiss_focusable=True))
        body.addWidget(self._build_names())
        body.addWidget(self._build_footer())
        self._col.addWidget(self._body)

        self._decision_row = done_row(self, "check", qcolor(INK_3), "")
        self._decision_row.hide()
        self._col.addWidget(self._decision_row)



    def _build_names(self) -> QWidget:
        host = QWidget(self._body)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_TIGHT)
        for item in self.items[:NAMES_SHOWN]:
            label = plain_label(str(item.get("name") or ""), host)
            label.setStyleSheet(_NAMES_QSS)
            label.setWordWrap(True)
            lay.addWidget(label)
        rest = len(self.items) - NAMES_SHOWN
        if rest > 0:
            more = plain_label(self.tr("and {n} more").format(n=rest), host)
            more.setStyleSheet(_NAMES_QSS)
            lay.addWidget(more)
        return host

    def _build_footer(self) -> QWidget:
        footer = QWidget(self._body)
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_OUTER)
        lay.addStretch(1)







        lay.addWidget(_pill(self._button(self.tr("Delete"), _BTN_GHOST_PILL,
                                         lambda: self._decide("delete"))))
        lay.addWidget(_pill(self._button(self.tr("Tidy up"), _BTN_PRIMARY_PILL,
                                         lambda: self._decide("group"))))
        return footer



    def _decide(self, decision: str) -> None:
        if self.decision is not None:
            return
        self.decision = decision
        self._body.setEnabled(False)
        ids = [str(i.get("layer_id")) for i in self.items if i.get("layer_id")]
        self.decided.emit(self.run_id, decision, ids)

    def collapse(self, sentence: str = "") -> None:
        """Fold shut, then the one line saying what happened to the layers."""
        text = " ".join(str(sentence or "").split()) or self.default_sentence()
        self._decision_row.icon_label.setPixmap(
            pixmap_for(self, "dash" if self.decision == "keep" else "check", 11, qcolor(INK_3)))
        self._decision_row.line_label.setText(text)
        self._body.setEnabled(False)
        self.fold_body(self._body, self._show_decision)

    def default_sentence(self) -> str:
        count = len(self.items)
        if self.decision == "delete":
            return (self.tr("Deleted {n} working layer").format(n=count) if count == 1
                    else self.tr("Deleted {n} working layers").format(n=count))
        if self.decision == "group":
            return (self.tr("Tidied {n} working layer away").format(n=count) if count == 1
                    else self.tr("Tidied {n} working layers away").format(n=count))
        return (self.tr("Kept {n} working layer").format(n=count) if count == 1
                else self.tr("Kept {n} working layers").format(n=count))

    def _show_decision(self) -> None:
        self._body.hide()
        self._decision_row.show()
        self.set_frame(_ASK_DONE_QSS)
        self.set_margins(0, 0, 0, 0)
        self.setMaximumWidth(_UNBOUNDED_PX)

    def to_markdown(self) -> str:
        state = self.decision or self.tr("pending")
        return f"- **{self.tr('Working layers')}:** {len(self.items)} ({state})"
