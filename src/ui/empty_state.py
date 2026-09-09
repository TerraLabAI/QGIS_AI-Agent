# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The empty thread."""













from __future__ import annotations

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .font_scale import scale_px_length, scale_qss_font_px
from .icons import icon_for, pixmap_for
from .style import (
    _BTN_PRIMARY,
    ACCENT_TINT,
    ACCENT_TINT_ON,
    BTN_PILL_PX,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    HAIRLINE,
    INK,
    MUTED,
    RADIUS_PANEL,
    SPACE_OUTER,
    SPACE_TIGHT,
    SURFACE,
    TINT_ON,
)

_HINT_QSS = f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent; border: none;"




_DISCLOSURE_QSS = scale_qss_font_px(
    f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent;"
    " border: none; padding: 0 8px;")





_QUESTION_QSS = scale_qss_font_px(
    f"font-size: {FONT_BASE + 4}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none;")


_ROW_QSS = scale_qss_font_px(
    f"QPushButton {{ background: transparent; color: {MUTED}; border: none;"
    f" border-radius: 14px; padding: 6px 12px; font-size: {FONT_BODY}px; text-align: left; }}"
    f"QPushButton:hover {{ background: {ACCENT_TINT}; color: {INK}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_TINT_ON}; }}"
)




_NOTICE_QSS = (
    f"QWidget#welcomeCard {{ background: {SURFACE}; border: 1px solid {HAIRLINE};"
    f" border-radius: {RADIUS_PANEL}px; }}"
    "QLabel { background: transparent; border: none; }"
)
_NOTICE_BODY_QSS = scale_qss_font_px(f"font-size: {FONT_BODY}px; color: {MUTED};")



_NOTICE_GLYPH_QSS = f"QLabel {{ background: {TINT_ON}; border: none; border-radius: 10px; }}"
_ROW_GLYPH = 18
_NOTICE_GLYPH_BOX = 20
_NOTICE_GLYPH = 13











WELCOME_ROWS = (
    ("eye", QT_TRANSLATE_NOOP("EmptyState", "Knows your project."),
     QT_TRANSLATE_NOOP("EmptyState", "Layers, fields, CRS, selection, view.")),
    ("pencil", QT_TRANSLATE_NOOP("EmptyState", "Does the work."),
     QT_TRANSLATE_NOOP("EmptyState", "Loads data, styles layers, runs analyses, builds maps.")),
    ("check", QT_TRANSLATE_NOOP("EmptyState", "Asks before it acts."),
     QT_TRANSLATE_NOOP("EmptyState", "Deleting, overwriting or spending. One click undoes a run.")),
)


def _row(parent: QWidget, glyph: str, text: str) -> QPushButton:
    button = QPushButton(text, parent)
    button.setStyleSheet(_ROW_QSS)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)
    button.setIcon(icon_for(button, glyph, _ROW_GLYPH))
    button.setIconSize(QSize(_ROW_GLYPH, _ROW_GLYPH))
    return button


class EmptyState(QWidget):
    """The question and the suggestion rows, plus the notice until the first run."""


    suggestion_clicked = pyqtSignal(str)
    welcome_dismissed = pyqtSignal()
    examples_requested = pyqtSignal()
    tutorial_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, SPACE_OUTER, 16, SPACE_OUTER)
        outer.setSpacing(SPACE_OUTER)
        outer.addStretch(3)

        block = QWidget(self)
        col = QVBoxLayout(block)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_OUTER + 4)

        question = QLabel(self.tr("What are we working on?"), block)
        question.setStyleSheet(_QUESTION_QSS)
        question.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        question.setWordWrap(True)
        col.addWidget(question)







        rows = QWidget(self)
        rows.setObjectName("emptyRows")
        rows_outer = QVBoxLayout(rows)
        rows_outer.setContentsMargins(0, 0, 0, 0)
        rows_outer.setSpacing(SPACE_TIGHT)
        pills = QWidget(rows)
        rows_col = QHBoxLayout(pills)
        rows_col.setContentsMargins(0, 0, 0, 0)
        rows_col.setSpacing(SPACE_TIGHT)
        rows_col.addStretch(1)






        self._examples_row = _row(pills, "book", self.tr("Examples"))
        self._examples_row.clicked.connect(self.examples_requested.emit)
        rows_col.addWidget(self._examples_row)
        self._tutorial_row = _row(pills, "play", self.tr("Tutorial"))
        self._tutorial_row.clicked.connect(self.tutorial_requested.emit)
        rows_col.addWidget(self._tutorial_row)
        rows_col.addStretch(1)
        rows_outer.addWidget(pills)














        disclosure = QLabel(
            self.tr("You are talking to an AI system. It can be wrong: check its changes."), rows)
        disclosure.setObjectName("aiDisclosure")
        disclosure.setStyleSheet(_DISCLOSURE_QSS)
        disclosure.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        disclosure.setWordWrap(True)
        rows_outer.addWidget(disclosure)
        self._disclosure = disclosure
        self._rows = rows
        self._mention_row = None

        outer.addWidget(block)
        outer.addStretch(4)



        self._welcome = self._build_welcome()
        self._welcome.hide()


        self._outer = outer
        self._suggestions: list[str] = []

    def rows_widget(self) -> QWidget:
        """The pill and the disclosure, for the panel to hang under the box."""





        return self._rows

    def set_centred(self, centred: bool) -> None:
        """Sit the question just above the composer, or float in the middle."""








        top, bottom = (1, 0) if centred else (3, 4)
        try:
            self._outer.setStretch(0, top)
            self._outer.setStretch(2, bottom)
        except (AttributeError, RuntimeError):
            pass

    def _build_welcome(self) -> QWidget:
        """What the assistant does, in a notice card, until the first run."""




        card = QWidget(self._rows)
        card.setObjectName("welcomeCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(_NOTICE_QSS)
        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(SPACE_TIGHT + 2)
        for icon, head, line in WELCOME_ROWS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(SPACE_TIGHT + 2)
            glyph_box = scale_px_length(_NOTICE_GLYPH_BOX)
            glyph = QLabel(card)
            glyph.setFixedSize(glyph_box, glyph_box)
            glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            glyph.setStyleSheet(_NOTICE_GLYPH_QSS)
            glyph.setPixmap(pixmap_for(glyph, icon, scale_px_length(_NOTICE_GLYPH)))
            row.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)


            words = QLabel(
                f'<b style="color:{INK}">{self.tr(head)}</b> {self.tr(line)}',
                card)
            words.setTextFormat(Qt.TextFormat.RichText)
            words.setStyleSheet(_NOTICE_BODY_QSS)
            words.setWordWrap(True)




            policy = words.sizePolicy()
            policy.setHeightForWidth(True)
            words.setSizePolicy(policy)


            words.setContentsMargins(0, 2, 0, 0)
            row.addWidget(words, 1, Qt.AlignmentFlag.AlignTop)
            col.addLayout(row)
        got_it = QPushButton(self.tr("Got it"), card)
        got_it.setStyleSheet(_BTN_PRIMARY)
        got_it.setFixedHeight(BTN_PILL_PX)
        got_it.setCursor(Qt.CursorShape.PointingHandCursor)
        got_it.setAutoDefault(False)
        got_it.clicked.connect(self._on_dismiss_welcome)
        col.addWidget(got_it, 0, Qt.AlignmentFlag.AlignRight)
        return card

    def set_welcome_visible(self, visible: bool) -> None:
        """Kept for the controller: the card is never shown any more."""
        del visible
        self._welcome.hide()

    def _on_dismiss_welcome(self) -> None:
        self._welcome.hide()
        self.welcome_dismissed.emit()



    def set_suggestions(self, items) -> None:
        """Kept for the controller: the project suggestions are stored, the empty state no longer lists them (the Examples library holds the same."""


        self._suggestions = [str(t) for t in (items or []) if str(t).strip()]

    def suggestions(self) -> list:
        return list(self._suggestions)
