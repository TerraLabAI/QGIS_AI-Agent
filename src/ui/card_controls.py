# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
)
from qgis.PyQt.QtGui import QIcon, QPainter, QPen
from qgis.PyQt.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from .card_base import reduced_motion
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .style import (
    _BTN_GHOST,
    _BTN_PRIMARY,
    _BTN_QUIET,
    ACCENT,
    BTN_SMALL_PX,
    FIELD,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    HOVER,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    MONO_FAMILY,
    MOTION_FOLD_MS,
    ON_ACCENT,
    RADIUS_CARD,
    RADIUS_CONTROL,
    SPACE_CARD,
    SURFACE,
    qcolor,
    repolish,
)
from .widgets import ChatLabel







_ASK_CARD_QSS = (
    "QWidget#{name} { background: " + SURFACE + "; border: 1px solid " + LINE + ";"
    f" border-radius: {RADIUS_CARD}px; }}"
)

_ASK_MARGINS = (16, 14, 16, 14)

_ASK_MAX_PX = 320
_PROPOSAL_MAX_PX = 380

_ASK_DONE_QSS = "QWidget#{name} { background: transparent; border: none; }"


_ASK_TEXT_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BASE}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none; }"
)

_ASK_LABEL_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; border: none; }}"
)

_ASK_DONE_LINE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none; }}"
)

_ASK_INPUT_QSS = scale_qss_font_px(
    f"QLineEdit {{ background: {FIELD}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 0 8px;"
    f" color: {INK}; font-size: {FONT_BODY}px; }}"
    f"QLineEdit:focus {{ border: 1px solid {ACCENT}; }}"
)

_PAGER_QSS = scale_qss_font_px(
    f"QLabel {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px; color: {INK_3};"
    " background: transparent; border: none; }"
)



_PILL_PX = BTN_SMALL_PX
_BTN_PRIMARY_PILL = _BTN_PRIMARY.replace("padding: 0 12px", "padding: 0 14px")
_BTN_GHOST_PILL = _BTN_GHOST
_BTN_QUIET_LINK = _BTN_QUIET





_ROW_PX = 28
_OPTION_ROW_QSS = (
    "QWidget#{name} { background: transparent; border: none;"
    f" border-radius: {RADIUS_CONTROL}px; }}"
    "QWidget#{name}:hover { background: " + HOVER + "; }"
    'QWidget#{name}[on="true"] { background: ' + HOVER_ON + "; }"
    "QLabel { background: transparent; border: none; }"
)
_OPTION_TEXT_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BASE}px; color: {INK}; background: transparent; border: none; }}"
)






_GLYPH_BTN_QSS = (
    "QToolButton { background: transparent; border: 1px solid transparent;"
    " border-radius: 5px; padding: 0; }"
    "QToolButton:hover { background: transparent; }"
    f'QToolButton[ring="true"] {{ border-color: {LINE_STRONG}; }}'
    f"QToolButton:focus {{ border-color: {ACCENT}; }}"
)


_MARK_PX = 16
_MARK_DOT_PX = 6
_MARK_RING_PX = 1.5
_MARK_CHECK_RADIUS = 4


def _pill(button):

    button.setFixedHeight(scale_px_length(_PILL_PX))
    return button


class _Mark(QWidget):









    def __init__(self, kind: str = "radio", parent=None, colour: str = ACCENT,
                 glyph: str = ON_ACCENT):
        super().__init__(parent)
        self.kind = kind
        self._colour = colour
        self._glyph = glyph
        self._checked = False
        self.setFixedSize(_MARK_PX, _MARK_PX)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, on: bool) -> None:
        if self._checked == bool(on):
            return
        self._checked = bool(on)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inset = _MARK_RING_PX / 2
        box = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        if self._checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(self._colour))
        else:
            painter.setPen(QPen(qcolor(LINE_STRONG), _MARK_RING_PX))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "radio":
            painter.drawEllipse(box)
        else:
            painter.drawRoundedRect(box, _MARK_CHECK_RADIUS, _MARK_CHECK_RADIUS)
        if not self._checked:
            return
        if self.kind == "radio":
            painter.setBrush(qcolor(self._glyph))
            offset = (_MARK_PX - _MARK_DOT_PX) / 2
            painter.drawEllipse(QRectF(offset, offset, _MARK_DOT_PX, _MARK_DOT_PX))
        else:
            check = pixmap_for(self, "check", 10, qcolor(self._glyph))
            painter.drawPixmap(3, 3, check)


class _GlyphButton(QToolButton):







    def __init__(self, glyph: str, glyph_px: int, button_px: int, parent=None, tooltip: str = "",
                 focusable: bool = False):
        super().__init__(parent)
        self._glyph = glyph
        self._glyph_px = glyph_px
        self._hovering = False
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)



        self.setFocusPolicy(Qt.FocusPolicy.TabFocus if focusable else Qt.FocusPolicy.NoFocus)
        self.setFixedSize(button_px, button_px)
        self.setIconSize(QSize(glyph_px, glyph_px))
        self.setProperty("ring", False)
        self.setStyleSheet(_GLYPH_BTN_QSS)
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        self._repaint()

    def set_ring(self, on: bool) -> None:
        if bool(self.property("ring")) == bool(on):
            return
        self.setProperty("ring", bool(on))
        repolish(self)

    def _repaint(self) -> None:
        try:
            colour = qcolor(INK if self._hovering else INK_3)
            if not self.isEnabled():
                colour.setAlphaF(0.3)
            self.setIcon(QIcon(pixmap_for(self, self._glyph, self._glyph_px, colour)))
        except (RuntimeError, AttributeError, TypeError):
            pass

    def enterEvent(self, event):  # noqa: N802
        super().enterEvent(event)
        self._hovering = True
        self._repaint()

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        self._hovering = False
        self._repaint()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        try:
            if event.type() in (QEvent.Type.EnabledChange, QEvent.Type.PaletteChange):
                self._repaint()
        except (RuntimeError, AttributeError):
            pass


def ask_head(parent: QWidget, text: str, on_dismiss=None, tooltip: str = "",
             dismiss_focusable: bool = False) -> QWidget:











    head = QWidget(parent)
    lay = QHBoxLayout(head)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(SPACE_CARD)
    label = ChatLabel(text, head, wrap=True, selectable=True)
    label.setObjectName("askText")
    label.setStyleSheet(_ASK_TEXT_QSS)
    lay.addWidget(label, 1, Qt.AlignmentFlag.AlignTop)
    button = None
    if on_dismiss is not None:
        button = _GlyphButton("close", 14, 20, head, tooltip, focusable=dismiss_focusable)
        button.clicked.connect(on_dismiss)
        lay.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
    head.text_label = label
    head.dismiss_button = button
    return head


def done_row(parent: QWidget, glyph: str, colour, text: str) -> QWidget:


    row = QWidget(parent)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(2, 0, 2, 0)
    lay.setSpacing(SPACE_CARD)
    icon = QLabel(row)
    icon.setFixedSize(14, 14)
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon.setPixmap(pixmap_for(row, glyph, 11, colour))
    lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)



    label = ChatLabel(text, row, wrap=True, selectable=True)
    label.setObjectName("decisionLine")
    label.setStyleSheet(_ASK_DONE_LINE_QSS)
    lay.addWidget(label, 1)
    row.line_label = label
    row.icon_label = icon
    return row







class FoldMixin:




    _fold_anim = None

    def fold_body(self, body: QWidget, done) -> None:
        self.stop_fold()
        try:



            visible = body.isVisible() and not reduced_motion(self)
            start = body.height()
        except (RuntimeError, AttributeError):
            visible, start = False, 0
        if not visible or start <= 0:
            done()
            return
        fold = QVariantAnimation(self)
        fold.setDuration(MOTION_FOLD_MS)
        fold.setEasingCurve(QEasingCurve.Type.OutCubic)
        fold.setStartValue(int(start))
        fold.setEndValue(0)
        fold.valueChanged.connect(lambda value: self._fold_step(body, value))
        fold.finished.connect(lambda: self._fold_done(done))
        self._fold_anim = fold
        fold.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    @staticmethod
    def _fold_step(body: QWidget, value) -> None:
        try:
            body.setMaximumHeight(max(0, int(value)))
        except (RuntimeError, TypeError):
            pass

    def _fold_done(self, done) -> None:
        fold = self._fold_anim
        self._fold_anim = None
        if fold is not None:
            try:
                fold.deleteLater()
            except (RuntimeError, AttributeError):
                pass
        try:
            done()
        except RuntimeError:
            pass

    def stop_fold(self) -> None:
        fold = self._fold_anim
        self._fold_anim = None
        if fold is None:
            return
        try:
            fold.stop()
            fold.deleteLater()
        except (RuntimeError, AttributeError):
            pass

    def cleanup(self) -> None:

        self.stop_fold()


class AnswerFooter(QWidget):











    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SPACE_CARD)
        self._grid.setVerticalSpacing(SPACE_CARD)
        self._link: QWidget | None = None
        self._answers: list[QWidget] = []
        self._answers_host: QWidget | None = None
        self._stacked: bool | None = None




        self._needed_cache: int | None = None


        self._link_full = ""
        self._link_shown: str | None = None

    def set_widgets(self, link, answers) -> None:

        self._link = link
        self._answers = [w for w in answers if w is not None]
        self._needed_cache = None
        self._link_shown = None
        for widget in ([link] if link is not None else []) + self._answers:
            widget.setParent(self)
        self._place(False)

    def _needed(self) -> int:
        if self._needed_cache is None:
            gap = self._grid.horizontalSpacing()
            width = sum(w.sizeHint().width() for w in self._answers)
            width += gap * max(len(self._answers) - 1, 0)
            if self._link is not None:
                parts = self._link_parts()
                width += (parts[1] + parts[2].horizontalAdvance(parts[0]) if parts
                          else self._link.sizeHint().width()) + gap
            self._needed_cache = width
        return self._needed_cache

    def _link_parts(self):






        link = self._link
        if not isinstance(link, QPushButton):
            return None
        shown = link.text()
        if shown != self._link_shown:
            self._link_full = shown
        link.ensurePolished()
        metrics = link.fontMetrics()
        widest = max(metrics.horizontalAdvance(line) for line in shown.split("\n"))
        return self._link_full, link.sizeHint().width() - widest, metrics

    def _fit_link(self) -> None:







        parts = self._link_parts()
        if parts is None:
            return
        words, frame, metrics = parts
        room = self.width() - frame
        text = words
        if self._stacked and metrics.horizontalAdvance(words) > room:
            lines = []
            for word in words.split():
                if lines and metrics.horizontalAdvance(lines[-1] + " " + word) <= room:
                    lines[-1] += " " + word
                else:
                    lines.append(word)
            text = "\n".join(lines)
        self._link_shown = text
        if self._link.text() != text:
            self._link.setText(text)

    def _place(self, stacked: bool) -> None:
        self._stacked = stacked
        for widget in ([self._link] if self._link is not None else []) + self._answers:
            self._grid.removeWidget(widget)





        if self._answers_host is not None:
            self._grid.removeWidget(self._answers_host)
            self._answers_host.hide()
            self._answers_host.deleteLater()
            self._answers_host = None
        for index in range(2):
            self._grid.setColumnStretch(index, 0)
        row = 0
        column = 0
        if self._link is not None:





            self._grid.addWidget(self._link, 0, 0, Qt.AlignmentFlag.AlignVCenter)
            if stacked:
                row = 1
            else:
                column = 1


        self._grid.setColumnStretch(column, 1)
        host = QWidget(self)
        host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        line = QHBoxLayout(host)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(self._grid.horizontalSpacing())
        line.addStretch(1)
        for widget in self._answers:
            line.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._answers_host = host
        self._grid.addWidget(host, row, column,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def minimumSizeHint(self):  # noqa: N802






        size = super().minimumSizeHint()
        widest = max([w.sizeHint().width() for w in self._answers] or [0])
        if self._link is not None:
            widest = max(widest, self._link.sizeHint().width())
        pills = sum(w.sizeHint().width() for w in self._answers)
        pills += self._grid.horizontalSpacing() * max(len(self._answers) - 1, 0)
        size.setWidth(max(widest, pills))
        return size

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        stacked = self.width() > 0 and self._needed() > self.width()
        if stacked != self._stacked:
            self._place(stacked)
        self._fit_link()


__all__ = [
    "AnswerFooter",
    "FoldMixin",
    "_ASK_CARD_QSS",
    "_ASK_DONE_LINE_QSS",
    "_ASK_DONE_QSS",
    "_ASK_INPUT_QSS",
    "_ASK_LABEL_QSS",
    "_ASK_MARGINS",
    "_ASK_MAX_PX",
    "_ASK_TEXT_QSS",
    "_BTN_GHOST_PILL",
    "_BTN_PRIMARY_PILL",
    "_BTN_QUIET_LINK",
    "_GlyphButton",
    "_MARK_PX",
    "_Mark",
    "_OPTION_ROW_QSS",
    "_OPTION_TEXT_QSS",
    "_PAGER_QSS",
    "_PILL_PX",
    "_PROPOSAL_MAX_PX",
    "_ROW_PX",
    "_pill",
    "ask_head",
    "done_row",
]
