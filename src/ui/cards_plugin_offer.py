# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The card that offers to install QuickMapServices, at the moment it is missed."""

















from __future__ import annotations

import contextlib

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .card_base import _UNBOUNDED_PX, _Card, plain_label
from .card_controls import (
    _ASK_CARD_QSS,
    _ASK_DONE_QSS,
    _ASK_MARGINS,
    _BTN_PRIMARY_PILL,
    FoldMixin,
    _pill,
    ask_head,
    done_row,
)
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .style import FONT_BODY, INK_2, INK_3, SPACE_OUTER, qcolor


_CARD_MAX_PX = 420




_SETTINGS_KEY = "TerraLab/AIAgent/plugin_offers_answered"

_BODY_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)


def answered_offers(settings=None) -> list:
    """The offer ids the user has already answered, install or not."""
    store = settings if settings is not None else QgsSettings()
    with contextlib.suppress(Exception):
        raw = store.value(_SETTINGS_KEY, [])
        if isinstance(raw, str):
            return [raw] if raw else []
        if isinstance(raw, (list, tuple)):
            return [str(v) for v in raw if str(v)]
    return []


def remember_answered(offer_id: str, settings=None) -> None:
    if not offer_id:
        return
    store = settings if settings is not None else QgsSettings()
    with contextlib.suppress(Exception):
        seen = answered_offers(store)
        if offer_id not in seen:
            store.setValue(_SETTINGS_KEY, seen + [offer_id])


class PluginOfferCard(_Card, FoldMixin):
    """One missing plugin, what its absence costs, and the one-click install."""







    decided = pyqtSignal(str, str)

    def __init__(self, offer_id: str, title: str, body: str, action: str, parent=None):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_OUTER)
        self.setMaximumWidth(scale_px_length(_CARD_MAX_PX))
        self.offer_id = str(offer_id or "")
        self.title = str(title or "")
        self.decision: str | None = None

        self._body = QWidget(self)
        body_col = QVBoxLayout(self._body)
        body_col.setContentsMargins(0, 0, 0, 0)
        body_col.setSpacing(SPACE_OUTER)
        body_col.addWidget(ask_head(self._body, self.title, lambda: self._decide("skip"),
                                    self.tr("Not now"), dismiss_focusable=True))
        line = plain_label(str(body or ""), self._body)
        line.setStyleSheet(_BODY_QSS)
        line.setWordWrap(True)
        body_col.addWidget(line)
        body_col.addWidget(self._build_footer(action))
        self._col.addWidget(self._body)

        self._decision_row = done_row(self, "check", qcolor(INK_3), "")
        self._decision_row.hide()
        self._col.addWidget(self._decision_row)

    def _build_footer(self, action: str) -> QWidget:
        footer = QWidget(self._body)
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_OUTER)
        lay.addStretch(1)



        lay.addWidget(_pill(self._button(str(action or self.tr("Install")), _BTN_PRIMARY_PILL,
                                         lambda: self._decide("install"))))
        return footer

    def _decide(self, decision: str) -> None:
        if self.decision is not None:
            return
        self.decision = decision
        self._body.setEnabled(False)
        remember_answered(self.offer_id)
        self.decided.emit(self.offer_id, decision)

    def collapse(self, sentence: str = "") -> None:
        text = " ".join(str(sentence or "").split()) or self.default_sentence()
        self._decision_row.icon_label.setPixmap(
            pixmap_for(self, "dash" if self.decision == "skip" else "check", 11, qcolor(INK_3)))
        self._decision_row.line_label.setText(text)
        self._body.setEnabled(False)
        self.fold_body(self._body, self._show_decision)

    def default_sentence(self) -> str:
        if self.decision == "install":
            return self.tr("Opened the plugin manager")
        return self.tr("Not now")

    def _show_decision(self) -> None:
        self._body.hide()
        self._decision_row.show()
        self.set_frame(_ASK_DONE_QSS)
        self.set_margins(0, 0, 0, 0)
        self.setMaximumWidth(_UNBOUNDED_PX)

    def to_markdown(self) -> str:
        state = self.decision or self.tr("pending")
        return f"- **{self.title}** ({state})"
