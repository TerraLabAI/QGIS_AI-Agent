# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





















from __future__ import annotations

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPointF,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QFont, QPalette
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .card_base import _UNBOUNDED_PX, _Card, plain_label, reduced_motion
from .card_controls import (
    _ASK_CARD_QSS,
    _ASK_DONE_QSS,
    _ASK_LABEL_QSS,
    _ASK_MARGINS,
    _BTN_GHOST_PILL,
    _BTN_PRIMARY_PILL,
    _OPTION_ROW_QSS,
    _OPTION_TEXT_QSS,
    _PAGER_QSS,
    _ROW_PX,
    AnswerFooter,
    FoldMixin,
    _GlyphButton,
    _Mark,
    _pill,
    ask_head,
)
from .cards_click import _ClickRow
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import icon_for
from .style import (
    ACCENT,
    ACCENT_INK,
    ACCENT_TINT_STRONG,
    FIELD,
    FONT_BASE,
    FONT_HINT,
    HAIRLINE,
    HOVER,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    RADIUS_CONTROL,
    SPACE_CARD,
    SPACE_OUTER,
    SPACE_TIGHT,
    USER_PILL_LINE,
    qcolor,
    repolish,
)




_FREE_TEXT_QSS = scale_qss_font_px(
    f"QLineEdit {{ background: transparent; border: 1px solid {HAIRLINE};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 5px 8px 5px 2px;"
    f" color: {INK}; font-size: {FONT_BASE}px; }}"
    f"QLineEdit:focus {{ border-color: {ACCENT}; }}"
)




_RECOMMENDED_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {ACCENT_INK}; background: {ACCENT_TINT_STRONG};"
    f" border: none; border-radius: 4px; padding: 1px 6px; margin-top: 1px; }}"
)


_WHY_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none; }}"
)

_ROW_STATES_QSS = (
    "QWidget#{name}:hover { background: " + HOVER + "; }"
    'QWidget#{name}[on="true"] { background: ' + HOVER_ON + "; }"
    'QWidget#{name}[down="true"] { background: ' + HOVER_ON + "; }"
)


_CONTINUE_DISABLED_QSS = f"QPushButton:disabled {{ background: {HOVER_ON}; color: {INK_3}; }}"

_RISE_PX = 6
_RISE_MS = 180



_COMPACT_ROW_PX = 32
_ROW_GAP_PX = 6



_HEADER_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; border: none; }}"
)


_SLIDE_PX = 24
_SLIDE_MS = 360

_ADVANCE_MS = 480





_SUMMARY_RADIUS = 18
_SUMMARY_QSS = scale_qss_font_px(
    f"QFrame#roundSummary {{ background: {FIELD}; border: 1px solid {USER_PILL_LINE};"
    f" border-radius: {_SUMMARY_RADIUS}px; }}"
    f"QFrame#roundSummary QLabel {{ background: transparent; border: none; font-size: {FONT_BASE}px; }}"
    f"QFrame#roundSummary QLabel#summaryHead {{ color: {INK_3}; }}"
    f"QFrame#roundSummary QLabel#summaryAnswer {{ color: {INK}; }}"
    f'QFrame#roundSummary QLabel#summaryAnswer[skipped="true"] {{ color: {INK_3}; }}'
)
_SUMMARY_SHARE = 0.85


class _RoundSummary(QWidget):




    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("roundSummaryRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        self._frame = QFrame(self)
        self._frame.setObjectName("roundSummary")
        self._frame.setStyleSheet(_SUMMARY_QSS)



        self._frame.setMinimumHeight(2 * _SUMMARY_RADIUS)

        self._lines = QGridLayout(self._frame)
        self._lines.setContentsMargins(12, 7, 12, 7)
        self._lines.setHorizontalSpacing(10)
        self._lines.setVerticalSpacing(3)
        self._lines.setColumnStretch(1, 1)
        row.addWidget(self._frame)
        self._items: list = []

    def add_line(self, head: str, answer: str) -> None:
        from .widgets import ElidedLabel

        self._items.append((head, answer))
        index = len(self._items) - 1
        label = ElidedLabel(head, self._frame)
        label.setObjectName("summaryHead")
        label.setMaximumWidth(scale_px_length(140))
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._lines.addWidget(label, index, 0, Qt.AlignmentFlag.AlignTop)
        text = plain_label(answer or self.tr("Skipped"), self._frame)
        text.setObjectName("summaryAnswer")
        text.setProperty("skipped", not answer)
        text.setWordWrap(True)
        self._lines.addWidget(text, index, 1)

    def lines(self) -> list:
        return list(self._items)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self.width() >= 60:
            self._frame.setMaximumWidth(int(self.width() * _SUMMARY_SHARE))


def _ease_out() -> QEasingCurve:

    try:
        curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
        curve.addCubicBezierSegment(QPointF(0.22, 1.0), QPointF(0.36, 1.0), QPointF(1.0, 1.0))
        return curve
    except (AttributeError, TypeError):
        return QEasingCurve(QEasingCurve.Type.OutCubic)


def _tr_option(text: str) -> str:

    from qgis.PyQt.QtCore import QCoreApplication

    return QCoreApplication.translate("QuestionCard", text)


def _as_options(options) -> list:




    if isinstance(options, (str, bytes)) or not hasattr(options, "__iter__"):
        return []
    return [str(o) for o in (options or [])]


class _OptionRow(_ClickRow):









    _serial = 0
    enter = pyqtSignal()
    step = pyqtSignal(int)

    def __init__(self, text: str, parent=None, kind: str = "radio", why: str = "",
                 detail: str = "", recommended: bool = False):
        super().__init__(parent)
        _OptionRow._serial += 1
        self.setObjectName(f"optionRow{_OptionRow._serial}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet((_OPTION_ROW_QSS + _ROW_STATES_QSS).replace("{name}", self.objectName()))
        self.setMinimumHeight(scale_px_length(_COMPACT_ROW_PX))
        self.setProperty("on", False)
        self.setProperty("rec", bool(recommended))
        self.setProperty("down", False)
        self.text = text




        self.setAccessibleName(" · ".join(part for part in (
            text, self.tr("Recommended") if recommended else "", detail, why) if part))
        self.setAccessibleDescription(
            _tr_option("Checkbox") if kind == "check" else _tr_option("Radio button"))
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 7, 8, 7)
        row.setSpacing(SPACE_CARD)
        self.mark = _Mark(kind, self)
        row.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        lines = QVBoxLayout()
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(0)
        label = plain_label(text, self)
        label.setObjectName("optionText")
        label.setStyleSheet(_OPTION_TEXT_QSS)
        label.setWordWrap(True)
        if recommended:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(SPACE_TIGHT)

            head.addWidget(label, 0)
            tag = QLabel(self.tr("Recommended"), self)
            tag.setObjectName("optionRecommended")
            tag.setStyleSheet(_RECOMMENDED_QSS)
            tag.setTextFormat(Qt.TextFormat.PlainText)
            head.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)
            head.addStretch(1)
            lines.addLayout(head)
        else:
            lines.addWidget(label)
        if detail:
            line = plain_label(detail, self)
            line.setObjectName("optionDetail")
            line.setStyleSheet(_ASK_LABEL_QSS)
            line.setWordWrap(True)
            lines.addWidget(line)
        if why:
            reason = plain_label(why, self)
            reason.setObjectName("optionWhy")
            reason.setStyleSheet(_WHY_QSS)
            reason.setWordWrap(True)
            lines.addWidget(reason)
        row.addLayout(lines, 1)

    def set_checked(self, on: bool) -> None:
        self.mark.set_checked(on)


        base = self.accessibleDescription().split(" · ")[0]
        self.setAccessibleDescription(
            f"{base} · {_tr_option('selected') if on else _tr_option('not selected')}")
        if bool(self.property("on")) != bool(on):
            self.setProperty("on", bool(on))
            repolish(self)

    def is_checked(self) -> bool:
        return self.mark.is_checked()

    def _set_down(self, down: bool) -> None:
        if bool(self.property("down")) != bool(down):
            self.setProperty("down", bool(down))
            repolish(self)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_down(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._set_down(False)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._set_down(False)
        super().leaveEvent(event)

    def keyPressEvent(self, event):  # noqa: N802


        key = event.key()
        if key == Qt.Key.Key_Space:
            self.clicked.emit()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.enter.emit()
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.step.emit(-1 if key == Qt.Key.Key_Up else 1)
        else:
            event.ignore()
            return
        event.accept()


class _FreeTextRow(QWidget):




    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.edit = QLineEdit(self)
        self.edit.setObjectName("questionFreeText")
        self.edit.setStyleSheet(_FREE_TEXT_QSS)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(False)
        self.edit.setMinimumHeight(scale_px_length(_ROW_PX + 4))
        try:
            pencil = icon_for(self.edit, "pencil", 14, qcolor(INK_3))
            self.edit.addAction(pencil, QLineEdit.ActionPosition.LeadingPosition)
        except (RuntimeError, AttributeError, TypeError):
            pass
        try:
            palette = self.edit.palette()
            palette.setColor(QPalette.ColorRole.PlaceholderText, qcolor(INK_3))
            self.edit.setPalette(palette)
        except (RuntimeError, AttributeError, TypeError):
            pass
        lay.addWidget(self.edit, 1)


class _CurrentStack(QWidget):






    def __init__(self, parent=None):
        super().__init__(parent)
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(0)
        self._pages: list = []
        self._index = -1

    def addWidget(self, page) -> None:  # noqa: N802
        self._pages.append(page)
        self._box.addWidget(page)
        if self._index < 0:
            self._index = 0
        page.setVisible(len(self._pages) - 1 == self._index)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not (0 <= index < len(self._pages)):
            return
        self._index = index
        for i, page in enumerate(self._pages):
            page.setVisible(i == index)
        self.updateGeometry()

    def currentWidget(self):  # noqa: N802
        return self._pages[self._index] if 0 <= self._index < len(self._pages) else None


class _Page(QWidget):




    changed = pyqtSignal()
    proceed = pyqtSignal()
    picked = pyqtSignal()
    submitted = pyqtSignal(str, str)

    def __init__(self, tool_call_id: str, question: str, options, allow_free_text: bool,
                 recommended: int = -1, why: str = "",
                 multiple: bool = False, parent=None, details=None, header: str = ""):
        super().__init__(parent)
        self.tool_call_id = tool_call_id
        self.header = " ".join(str(header or "").split())[:12].strip()

        self.skipped = False


        self.seen = False
        self.question = (question or "").strip()
        self.options = _as_options(options)
        self.multiple = bool(multiple)
        details = _as_options(details)
        self.details = (details + [""] * len(self.options))[:len(self.options)]
        self.answer: str | None = None
        try:
            index = int(recommended)
        except (TypeError, ValueError):
            index = -1
        self.recommended = index if 0 <= index < len(self.options) else -1
        self.why = " ".join((why or "").split())
        self._rows: list = []
        self._free: _FreeTextRow | None = None

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_CARD)


        if self.header:
            label = plain_label(self.header.upper(), self)
            label.setObjectName("questionHeader")
            label.setStyleSheet(_HEADER_QSS)
            label.ensurePolished()
            font = label.font()
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
            label.setFont(font)
            col.addWidget(label)
        self.head = ask_head(self, self.question)
        col.addWidget(self.head)
        if self.multiple and self.options:
            hint = plain_label(self.tr("Select all that apply"), self)
            hint.setObjectName("questionHint")
            hint.setStyleSheet(_ASK_LABEL_QSS)
            col.addWidget(hint)
        if self.options:
            col.addWidget(self._build_options())

            col.addSpacing(SPACE_TIGHT)


        self._free = _FreeTextRow(self.tr("Write my own answer…") if self.options
                                  else self.tr("Type your answer"), self)
        self._free.edit.textEdited.connect(self._on_typed)



        self._free.edit.installEventFilter(self)
        col.addWidget(self._free)

        col.addStretch(1)

    def _build_options(self) -> QWidget:
        host = QWidget(self)
        rows = QVBoxLayout(host)
        rows.setContentsMargins(0, 0, 0, 0)

        rows.setSpacing(_ROW_GAP_PX)
        kind = "check" if self.multiple else "radio"
        for i, option in enumerate(self.options):
            row = _OptionRow(option, host, kind, self.why if i == self.recommended else "",
                             self.details[i], i == self.recommended)
            row.clicked.connect(lambda i=i: self.pick(i))
            row.enter.connect(lambda i=i: self._enter_on(i))
            row.step.connect(lambda d, i=i: self._focus_row(i + d))
            rows.addWidget(row)
            self._rows.append(row)
        return host



    def pick(self, index: int) -> None:

        if self.answer is not None or not (0 <= index < len(self._rows)):
            return
        if self.multiple:
            self._rows[index].set_checked(not self._rows[index].is_checked())
        else:
            for i, row in enumerate(self._rows):
                row.set_checked(i == index)
        if self._free is not None:
            self._free.edit.clear()
        self.skipped = False
        self.changed.emit()
        if not self.multiple:
            self.picked.emit()

    def _on_typed(self, text: str) -> None:

        if text:
            self.skipped = False
            for row in self._rows:
                row.set_checked(False)
        self.changed.emit()

    def eventFilter(self, obj, event):  # noqa: N802
        if self._free is not None and obj is self._free.edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._on_return()
                return True
            if key == Qt.Key.Key_Up and self._rows:
                self._focus_row(len(self._rows) - 1)
                return True
        return super().eventFilter(obj, event)

    def _focus_row(self, index: int) -> None:

        if 0 <= index < len(self._rows):
            self._rows[index].setFocus(Qt.FocusReason.TabFocusReason)
        elif index >= len(self._rows) and self._free is not None:
            self._free.edit.setFocus(Qt.FocusReason.TabFocusReason)

    def _enter_on(self, index: int) -> None:

        if self.answer is not None:
            return
        if not (0 <= index < len(self._rows)):
            return
        typed = self._free.edit.text().strip() if self._free is not None else ""
        if self.multiple:
            nothing_yet = not any(row.is_checked() for row in self._rows)
        else:
            nothing_yet = not self._rows[index].is_checked()
        if nothing_yet and not typed:
            self.pick(index)
        if self.is_answerable():
            self.proceed.emit()

    def _on_return(self) -> None:
        if self.answer is not None:
            return
        if not self.is_answerable() and self.recommended >= 0:

            for i, row in enumerate(self._rows):
                row.set_checked(i == self.recommended)
            self.skipped = False
            self.changed.emit()
        if self.is_answerable():
            self.proceed.emit()

    def answer_text(self) -> str:


        typed = self._free.edit.text().strip() if self._free is not None else ""
        if typed:
            return typed
        chosen = [row.text for row in self._rows if row.is_checked()]
        return ", ".join(chosen)

    def is_answerable(self) -> bool:
        return self.answer is None and bool(self.answer_text())

    def focus(self) -> None:
        if self._free is not None:
            self._free.edit.setFocus()



    def submit(self, answer: str) -> None:

        if self.answer is not None:
            return
        self.settle(answer)
        self.submitted.emit(self.tool_call_id, answer)

    def settle(self, answer: str) -> None:


        if self.answer is not None:
            return
        self.answer = answer or ""
        self.setEnabled(False)

    def summary_head(self) -> str:

        return self.header or self.question

    def shown_answer(self) -> str:
        return self.answer or self.tr("skipped")


class QuestionCard(_Card, FoldMixin):











    answered = pyqtSignal(str, str)

    def __init__(self, tool_call_id: str, question: str, options=None,
                 allow_free_text: bool = True, parent=None, recommended: int = -1,
                 why: str = "", multiple: bool = False, details=None,
                 header: str = ""):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_CARD)


        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.tool_call_id = tool_call_id
        self.question = (question or "").strip()




        if isinstance(options, (str, bytes)) or not hasattr(options, "__iter__"):
            options = []
        self.options = [str(o) for o in (options or [])]
        self._pages: list = []
        self._current = 0
        self._folding = False
        self._sending = False
        self._slide: QVariantAnimation | None = None
        self._advance = QTimer(self)
        self._advance.setSingleShot(True)
        self._advance.setInterval(_ADVANCE_MS)
        self._advance.timeout.connect(self._auto_advance)

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACE_OUTER)
        self._stack = _CurrentStack(self._body)
        body.addWidget(self._stack)
        body.addWidget(self._build_footer())
        self._col.addWidget(self._body)

        self._done = QWidget(self)
        self._done_col = QVBoxLayout(self._done)
        self._done_col.setContentsMargins(0, 0, 0, 0)
        self._done_col.setSpacing(SPACE_TIGHT)
        self._done.hide()
        self._col.addWidget(self._done)

        self.add_page(tool_call_id, self.question, self.options, allow_free_text,
                      recommended=recommended, why=why, multiple=multiple,
                      details=details, header=header)



    def _build_footer(self) -> QWidget:
        footer = AnswerFooter(self._body)
        self._pager = QWidget(footer)
        pager = QHBoxLayout(self._pager)
        pager.setContentsMargins(0, 0, 0, 0)
        pager.setSpacing(SPACE_TIGHT)
        self._prev_btn = _GlyphButton("chevron_up", 14, 18, self._pager, self.tr("Previous question"),
                                      focusable=True)
        self._prev_btn.clicked.connect(lambda: self._go(self._current - 1))
        self._counter = QLabel("1/1", self._pager)
        self._counter.setStyleSheet(_PAGER_QSS)
        self._next_btn = _GlyphButton("chevron_down", 14, 18, self._pager, self.tr("Next question"),
                                      focusable=True)
        self._next_btn.clicked.connect(lambda: self._go(self._current + 1))
        pager.addWidget(self._prev_btn)
        pager.addWidget(self._counter)
        pager.addWidget(self._next_btn)
        self._pager.hide()




        skip = _pill(self._button(self.tr("Skip"), _BTN_GHOST_PILL, self._skip))
        self._continue_btn = _pill(self._button(self.tr("Continue"), _BTN_PRIMARY_PILL + _CONTINUE_DISABLED_QSS,
                                                self._continue))
        footer.set_widgets(self._pager, [skip, self._continue_btn])
        return footer

    def add_page(self, tool_call_id: str, question: str, options=None,
                 allow_free_text: bool = True, recommended: int = -1, why: str = "",
                 multiple: bool = False, details=None,
                 header: str = "") -> None:





        if self._page(tool_call_id) is not None:
            return
        page = _Page(tool_call_id, question, options, allow_free_text, recommended, why,
                     multiple, self._stack, details, header)
        page.changed.connect(self._refresh)
        page.proceed.connect(self._continue)
        page.picked.connect(self._advance.start)
        page.submitted.connect(self._on_submitted)
        self._pages.append(page)
        page.seen = len(self._pages) - 1 == self._current
        self._stack.addWidget(page)
        self._fit_policies()
        self._pager.setVisible(len(self._pages) > 1)
        self._refresh()



    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_risen", False):
            return
        self._risen = True
        if reduced_motion(self):
            return
        margins = self._col.contentsMargins()
        rise = QVariantAnimation(self)
        rise.setDuration(_RISE_MS)
        rise.setEasingCurve(_ease_out())
        rise.setStartValue(float(_RISE_PX))
        rise.setEndValue(0.0)

        def step(value, m=margins):
            try:
                r = int(round(float(value)))

                self._col.setContentsMargins(m.left(), m.top() + r, m.right(), max(0, m.bottom() - r))
            except (RuntimeError, TypeError):
                pass

        rise.valueChanged.connect(step)
        self._rise = rise
        rise.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _page(self, tool_call_id: str = ""):
        for page in self._pages:
            if page.tool_call_id == tool_call_id:
                return page
        return None

    def _current_page(self):
        return self._pages[self._current] if 0 <= self._current < len(self._pages) else None

    def _go(self, index: int) -> None:
        if not (0 <= index < len(self._pages)):
            return
        self._advance.stop()
        before, direction = self._stack.height(), (1 if index > self._current else -1)
        self._current = index
        self._pages[index].seen = True
        self._stack.setCurrentIndex(index)
        self._fit_policies()
        self._refresh()
        self._slide_to(before, direction)

    def _fit_policies(self) -> None:

        self._stack.updateGeometry()
        self.updateGeometry()

    def _slide_to(self, before: int, direction: int) -> None:

        if self._slide is not None:
            self._slide.stop()
            self._slide_done()
        page = self._current_page()
        if page is None or before <= 0 or reduced_motion(self):
            return
        width = self._stack.width()
        target = page.heightForWidth(width) if page.hasHeightForWidth() else -1
        target = target if target > 0 else page.sizeHint().height()
        slide = QVariantAnimation(self)
        slide.setDuration(_SLIDE_MS)
        slide.setEasingCurve(_ease_out())
        slide.setStartValue(0.0)
        slide.setEndValue(1.0)

        def step(value, page=page):
            try:
                t = float(value)
                self._stack.setFixedHeight(int(before + (target - before) * t))
                page.move(0, int(direction * _SLIDE_PX * (1.0 - t)))
            except (RuntimeError, TypeError):
                pass

        slide.valueChanged.connect(step)
        slide.finished.connect(self._slide_done)
        self._slide = slide
        step(0.0)
        slide.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _slide_done(self) -> None:
        self._slide = None
        try:
            self._stack.setMinimumHeight(0)
            self._stack.setMaximumHeight(_UNBOUNDED_PX)
            self._stack.updateGeometry()
            page = self._current_page()
            if page is not None:
                page.move(0, 0)
        except RuntimeError:
            pass

    def _next_open(self) -> int:


        order = list(range(self._current + 1, len(self._pages))) + list(range(0, self._current + 1))
        for i in order:
            if self._pages[i].answer is None:
                return i
        return -1

    def _refresh(self) -> None:


        page = self._current_page()
        count = len(self._pages)
        self._counter.setText(f"{self._current + 1} / {count}")
        self._prev_btn.setEnabled(self._current > 0)
        self._next_btn.setEnabled(self._current < count - 1)
        self._next_btn.set_ring(self._current < count - 1 and page is not None
                                and (page.is_answerable() or page.answer is not None))
        last = self._current >= count - 1
        self._continue_btn.setText(self.tr("Send") if last else self.tr("Continue"))
        self._continue_btn.setEnabled(page is not None and page.is_answerable())



    @property
    def answer(self):

        return self._pages[0].answer if self._pages else None

    @property
    def recommended(self) -> int:
        return self._pages[0].recommended if self._pages else -1

    @property
    def why(self) -> str:
        return self._pages[0].why if self._pages else ""

    def is_open(self) -> bool:

        return any(page.answer is None for page in self._pages)

    def is_answered(self, tool_call_id: str) -> bool:
        page = self._page(tool_call_id)
        return page is not None and page.answer is not None

    def focus(self) -> None:
        page = self._current_page()
        if page is not None and page._free is not None:
            page.focus()
        else:
            self.setFocus()

    def keyPressEvent(self, event):  # noqa: N802


        page = self._current_page()
        key = event.key()
        if page is not None and page.answer is None:
            if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
                page.pick(key - Qt.Key.Key_1)
                return
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and page._rows:
                page._focus_row(0 if key == Qt.Key.Key_Down else len(page._rows) - 1)
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                page._on_return()
                return
        super().keyPressEvent(event)

    def _continue(self) -> None:

        page = self._current_page()
        if page is None or not page.is_answerable():
            return
        self._move_on()

    def _skip(self) -> None:
        page = self._current_page()
        if page is None or page.answer is not None:
            return
        page.skipped = True
        self._move_on()

    def _auto_advance(self) -> None:


        page = self._current_page()
        if page is not None and page.is_answerable() and self._current < len(self._pages) - 1:
            self._go(self._current + 1)
            self.focus()

    def _move_on(self) -> None:
        self._advance.stop()
        if self._current < len(self._pages) - 1:
            self._go(self._current + 1)
            self.focus()
            return
        self._send()

    def _send(self) -> None:



        for i, page in enumerate(self._pages):
            if page.answer is None and (not page.seen or (not page.skipped and not page.is_answerable())):
                self._go(i)
                self.focus()
                return
        self._sending = True
        try:
            for page in list(self._pages):
                if page.answer is None:
                    page.submit("" if page.skipped else page.answer_text())
        finally:
            self._sending = False
        self._after_answer()

    def _on_submitted(self, tool_call_id: str, answer: str) -> None:
        self.answered.emit(tool_call_id, answer)
        self._after_answer()

    def _after_answer(self) -> None:

        if self._sending:
            return
        following = self._next_open()
        if following >= 0:
            self._go(following)



            self.focus()
            return
        self._refresh()
        self._fold()

    def collapse(self, answer: str, tool_call_id: str = "") -> None:


        page = self._page(tool_call_id) if tool_call_id else self._current_page()
        if page is None:
            return
        page.settle(answer)
        self._after_answer()

    def _fold(self) -> None:



        if self._folding or self._done.isVisible():
            return
        self._folding = True


        self._summary = _RoundSummary(self._done)
        for page in self._pages:
            self._summary.add_line(page.summary_head(), page.answer or "")
        self._done_col.addWidget(self._summary)
        self._body.setEnabled(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fold_body(self._body, self._show_done)

    def _show_done(self) -> None:





        body, self._body = self._body, None
        self._done.show()
        if body is not None:
            body.hide()
            body.setParent(None)
            body.deleteLater()
        self.set_frame(_ASK_DONE_QSS)
        self.set_margins(0, 0, 0, 0)
        self.setMaximumWidth(_UNBOUNDED_PX)

    def add_answered(self, question: str, answer: str, header: str = "") -> None:


        summary = getattr(self, "_summary", None)
        if summary is None:
            return
        head = " ".join(str(header or "").split())[:12].strip() or (question or "").strip()
        summary.add_line(head, answer or "")

    def to_markdown(self) -> str:
        lines = []
        for page in self._pages:
            answer = self.tr("pending") if page.answer is None else (page.answer or self.tr("skipped"))
            lines.append(f"- **{self.tr('Question')}:** {page.question} ({answer})")
        return "\n".join(lines)
