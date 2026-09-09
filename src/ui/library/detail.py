# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The page behind one card: one object, its run, and the way out."""
































from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..font_scale import scale_px_length, scale_qss_font_px
from ..icons import icon_for
from ..permission_chip import glyph_tile
from ..settings_pages import SCROLL_QSS
from ..shared import tr
from ..style import (
    _BTN_PRIMARY_WIDE,
    BTN_PRIMARY_WIDE_PX,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    HAIRLINE,
    INSET,
    MUTED,
)
from ..use_cases import use_case_groups
from ..widgets import FlowLayout
from .common import accent_of
from .scenes import scene_pixmap



_COLUMN = 660

_PAGE_MARGIN = 24
_TILE = 40
_TILE_GLYPH = 20



_SCENE_W, _SCENE_H = 376, 141
_CONNECTOR_GLYPH = 14
_CONNECTOR_TILE = 20

_TITLE_QSS = scale_qss_font_px(f"font-size: {FONT_BASE + 6}px; font-weight: 600; color: palette(text);"
                               " background: transparent;")
_EYEBROW_QSS = scale_qss_font_px(f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent;")
_OUTCOME_QSS = scale_qss_font_px(f"font-size: {FONT_BASE}px; color: {MUTED}; background: transparent;")



_CARD_QSS = scale_qss_font_px(
    f"QFrame#caseCard {{ background: palette(base); border: 1px solid {HAIRLINE};"
    " border-radius: 14px; }"
    f"QFrame#caseHead {{ background: {INSET}; border: none;"
    " border-top-left-radius: 13px; border-top-right-radius: 13px;"
    f" border-bottom: 1px solid {HAIRLINE}; }}"
    f"QFrame#caseRule {{ background: {HAIRLINE}; border: none; max-height: 1px; min-height: 1px; }}"
    f"QLabel#promptText {{ font-size: {FONT_BASE}px; color: palette(text);"
    " background: transparent; }"
    f"QLabel#promptCaption {{ font-size: {FONT_MICRO}px; font-weight: 600; color: {MUTED};"
    " background: transparent; }"
)

_META_QSS = scale_qss_font_px(
    f"QFrame#metaRule {{ background: {HAIRLINE}; border: none; max-height: 1px; min-height: 1px; }}"
    f"QLabel#caseCaveat {{ font-size: {FONT_MICRO}px; color: {MUTED}; background: transparent; }}"
)
_CONNECTOR_QSS = scale_qss_font_px(
    f"QFrame#connectorPill {{ background: transparent; border: 1px solid {HAIRLINE};"
    " border-radius: 15px; }"
    f"QLabel#connectorName {{ font-size: {FONT_BODY}px; color: palette(text);"
    " background: transparent; }"
)
_BACK_QSS = scale_qss_font_px(
    f"QPushButton {{ background: transparent; border: none; color: {MUTED};"
    f" font-size: {FONT_BODY}px; padding: 2px 4px; text-align: left; }}"
    "QPushButton:hover { color: palette(text); }"
)


class SceneBand(QFrame):
    """The card's head: the drawing, redrawn at whatever width it is given."""







    def __init__(self, scene: str, accent: str, outcome: str, owner, parent=None):
        super().__init__(parent)
        self.setObjectName("caseHead")
        self._scene, self._accent = scene, accent




        self._owner = owner
        self._drawn = -1
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        self._art = QLabel(self)
        self._art.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._art.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)


        self._art.setAccessibleName(outcome)
        row.addWidget(self._art, 1)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        margins = self.layout().contentsMargins()
        room = self.width() - margins.left() - margins.right()
        width = max(140, min(scale_px_length(_SCENE_W), room))
        if width == self._drawn:
            return
        height = int(round(width * _SCENE_H / _SCENE_W))
        pixmap = scene_pixmap(self._owner, self._scene, self._accent, width, height)
        if pixmap is None:
            return
        self._drawn = width
        self._art.setPixmap(pixmap)
        self._art.setFixedHeight(height)


class ExampleDetail(QWidget):
    """One use case, opened from its card."""

    back_requested = pyqtSignal()
    prompt_chosen = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.case = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._back = QPushButton(tr("Back to examples"), self)
        self._back.setStyleSheet(_BACK_QSS)


        self._back.setIcon(icon_for(self, "chevron_left", 12))
        self._back.setIconSize(QSize(12, 12))



        self._back.setAutoDefault(False)
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.clicked.connect(self.back_requested.emit)
        head = QWidget(self)
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(16, 12, 20, 2)
        head_row.addWidget(self._back, 0, Qt.AlignmentFlag.AlignLeft)
        head_row.addStretch(1)
        outer.addWidget(head)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("libraryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(SCROLL_QSS)
        self._body = QWidget(self._scroll)




        self._page = QHBoxLayout(self._body)
        self._page.setContentsMargins(_PAGE_MARGIN, 6, _PAGE_MARGIN, 26)
        self._page.setSpacing(0)
        self._column = QWidget(self._body)
        self._col = QVBoxLayout(self._column)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(scale_px_length(16))
        self._page.addWidget(self._column, 1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)
        self._centre()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._centre()

    def _centre(self) -> None:
        """Keep the reading column at its measure, centred in the window."""
        margin = scale_px_length(_PAGE_MARGIN)
        room = self._scroll.viewport().width() - 2 * margin
        extra = max(0, room - scale_px_length(_COLUMN))
        side = margin + extra // 2
        self._page.setContentsMargins(side, 6, side, 26)



    def _clear(self) -> None:
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def set_case(self, case) -> None:
        """Paint one case: what it is, what will be sent, then how it runs."""
        self.case = case
        self._clear()
        if case is None:
            return
        self._scroll.verticalScrollBar().setValue(0)
        self._col.addWidget(self._header(case))


        self._col.addWidget(self._case_card(case))
        meta = self._meta(case)
        if meta is not None:
            self._col.addWidget(meta)
        self._col.addStretch(1)

    def _header(self, case) -> QWidget:
        head = QWidget(self._column)
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(13)
        row.addWidget(glyph_tile(head, case.glyph, accent_of(case.group), _TILE, _TILE_GLYPH),
                      0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        eyebrow = QLabel(dict(use_case_groups()).get(case.group, case.group), head)
        eyebrow.setWordWrap(True)
        eyebrow.setStyleSheet(_EYEBROW_QSS)
        col.addWidget(eyebrow)
        title = QLabel(str(case.title or ""), head)
        title.setWordWrap(True)
        title.setStyleSheet(_TITLE_QSS)
        col.addWidget(title)
        outcome = QLabel(str(case.outcome or ""), head)
        outcome.setWordWrap(True)
        outcome.setStyleSheet(_OUTCOME_QSS)
        col.addWidget(outcome)
        row.addLayout(col, 1)
        return head



    def _case_card(self, case) -> QWidget:
        card = QFrame(self._column)
        card.setObjectName("caseCard")
        card.setStyleSheet(_CARD_QSS)
        col = QVBoxLayout(card)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        head = self._scene(case, card)
        if head is not None:
            col.addWidget(head)
        col.addWidget(self._prompt_body(str(case.prompt or ""), card))
        rule = QFrame(card)
        rule.setObjectName("caseRule")
        col.addWidget(rule)
        col.addWidget(self._footer(str(case.prompt or ""), card))
        return card

    def _scene(self, case, card) -> QWidget:
        """The card's head, or None when there is no drawing for this case."""






        scene = str(getattr(case, "scene", "") or "")
        accent = accent_of(case.group)


        pixmap = scene_pixmap(self, scene, accent, _SCENE_W, _SCENE_H)
        if pixmap is None:
            return None
        return SceneBand(scene, accent, str(case.outcome or ""), self, card)

    def _prompt_body(self, prompt: str, card) -> QWidget:
        body = QWidget(card)
        col = QVBoxLayout(body)
        col.setContentsMargins(18, 15, 18, 16)
        col.setSpacing(8)
        caption = QLabel(tr("The prompt"), body)
        caption.setObjectName("promptCaption")
        col.addWidget(caption)
        text = QLabel(prompt, body)
        text.setObjectName("promptText")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(text)
        return body

    def _footer(self, prompt: str, card) -> QWidget:
        """The card's commitment, and the only control on the page."""
        footer = QWidget(card)
        row = QHBoxLayout(footer)
        row.setContentsMargins(14, 10, 14, 12)
        row.setSpacing(8)
        row.addStretch(1)
        use = QPushButton(tr("Use this prompt"), footer)


        use.setStyleSheet(_BTN_PRIMARY_WIDE)
        use.setMinimumHeight(scale_px_length(BTN_PRIMARY_WIDE_PX))
        use.setCursor(Qt.CursorShape.PointingHandCursor)
        use.setAutoDefault(True)
        use.setDefault(True)
        use.clicked.connect(lambda: self.prompt_chosen.emit(prompt))
        row.addWidget(use, 0)
        return footer



    def _meta(self, case) -> QWidget:
        """The last strip: the providers that answer when the project turns out to be empty, and the one condition that binds the reader afterwards."""








        sources = self._connector_rows(case)
        caveat = str(getattr(case, "caveat", "") or "")
        if not sources and not caveat:
            return None
        block = QWidget(self._column)
        block.setStyleSheet(_META_QSS)
        col = QVBoxLayout(block)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(12)
        rule = QFrame(block)
        rule.setObjectName("metaRule")
        col.addWidget(rule)
        if sources:
            row = self._connectors(sources, block)
            row.setToolTip(tr("Where the data comes from"))
            col.addWidget(row)
        if caveat:
            col.addWidget(self._caveat(caveat, block))
        return block

    @staticmethod
    def _connector_rows(case) -> list:
        """``(name, glyph)`` per connector the case falls back to."""







        wanted = [str(i) for i in getattr(case, "connectors", ()) or [] if str(i)]
        if not wanted:
            return []
        try:
            from ..shared import get_connectors
        except ImportError:
            return []
        known = {str(row.get("id")): row for row in (get_connectors() or [])}
        if not known:
            return []
        rows = []
        for connector in wanted:
            row = known.get(connector)
            if row is None:
                continue
            rows.append((str(row.get("name") or connector),
                         str(row.get("glyph") or "globe")))
        return rows

    def _connectors(self, rows, parent) -> QWidget:
        block = QWidget(parent)


        line = FlowLayout(block, 8, 6)
        for name, glyph in rows:
            pill = QFrame(block)
            pill.setObjectName("connectorPill")
            pill.setStyleSheet(_CONNECTOR_QSS)
            inner = QHBoxLayout(pill)
            inner.setContentsMargins(5, 4, 11, 4)
            inner.setSpacing(7)
            tile = glyph_tile(pill, glyph, accent_of(self.case.group),
                              _CONNECTOR_TILE, _CONNECTOR_GLYPH)
            inner.addWidget(tile, 0)
            label = QLabel(name, pill)
            label.setObjectName("connectorName")
            inner.addWidget(label, 0)
            line.addWidget(pill)
        return block

    def _caveat(self, text: str, parent) -> QWidget:
        """The licence line, last on the page."""





        label = QLabel(text, parent)
        label.setObjectName("caseCaveat")
        label.setWordWrap(True)
        return label
