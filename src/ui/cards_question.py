# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The question card: the agent asks before it guesses."""



















from __future__ import annotations

from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QPalette
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .card_base import _UNBOUNDED_PX, _Card, plain_label
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
    done_row,
)
from .cards_click import _ClickRow
from .font_scale import scale_px_length, scale_qss_font_px
from .style import (
    FONT_BASE,
    INK,
    INK_3,
    SPACE_CARD,
    SPACE_OUTER,
    SPACE_TIGHT,
    qcolor,
    repolish,
)


_FREE_TEXT_QSS = scale_qss_font_px(
    "QLineEdit { background: transparent; border: none; padding: 0 6px;"
    f" color: {INK}; font-size: {FONT_BASE}px; }}"
)






_MAX_TIMEOUT_S = 3600


def _tr_option(text: str) -> str:
    """Words the accessibility layer reads, never painted on screen."""
    from qgis.PyQt.QtCore import QCoreApplication

    return QCoreApplication.translate("QuestionCard", text)


def _as_timeout(value) -> int:
    """The seconds a page may wait on its own, 0 when it must not."""






    try:
        seconds = int(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0
    if seconds <= 0:
        return 0
    return min(seconds, _MAX_TIMEOUT_S)


def _as_options(options) -> list:
    """The options as strings."""



    if isinstance(options, (str, bytes)) or not hasattr(options, "__iter__"):
        return []
    return [str(o) for o in (options or [])]


class _OptionRow(_ClickRow):
    """One choice of a question: the 16 px control, the label, and for the recommended one its reason in the third ink under the label."""







    _serial = 0

    def __init__(self, text: str, parent=None, kind: str = "radio", why: str = ""):
        super().__init__(parent)
        _OptionRow._serial += 1
        self.setObjectName(f"optionRow{_OptionRow._serial}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_OPTION_ROW_QSS.replace("{name}", self.objectName()))
        self.setMinimumHeight(scale_px_length(_ROW_PX))
        self.setProperty("on", False)
        self.text = text




        self.setAccessibleName(f"{text} · {why}" if why else text)
        self.setAccessibleDescription(
            _tr_option("Checkbox") if kind == "check" else _tr_option("Radio button"))
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 8, 4)
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
        lines.addWidget(label)
        if why:
            reason = plain_label(why, self)
            reason.setObjectName("optionWhy")
            reason.setStyleSheet(_ASK_LABEL_QSS)
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


class _FreeTextRow(QWidget):
    """The "Something else..." row: a field with no border that becomes the answer as soon as something is typed in it."""


    _serial = 0

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        _FreeTextRow._serial += 1
        self.setObjectName(f"freeTextRow{_FreeTextRow._serial}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_OPTION_ROW_QSS.replace("{name}", self.objectName()))
        self.setMinimumHeight(scale_px_length(_ROW_PX))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(0)
        self.edit = QLineEdit(self)
        self.edit.setStyleSheet(_FREE_TEXT_QSS)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(False)
        try:
            palette = self.edit.palette()
            palette.setColor(QPalette.ColorRole.PlaceholderText, qcolor(INK_3))
            self.edit.setPalette(palette)
        except (RuntimeError, AttributeError, TypeError):
            pass
        lay.addWidget(self.edit, 1)

    def mousePressEvent(self, event):  # noqa: N802 - Qt override

        self.edit.setFocus()
        super().mousePressEvent(event)


class _Page(QWidget):
    """One question of the card: the head, its rows, its field, its own countdown."""



    changed = pyqtSignal()
    submitted = pyqtSignal(str, str)
    auto_answered = pyqtSignal(str, int)

    def __init__(self, tool_call_id: str, question: str, options, allow_free_text: bool,
                 recommended: int = -1, why: str = "", timeout_s: int = 0,
                 multiple: bool = False, parent=None):
        super().__init__(parent)
        self.tool_call_id = tool_call_id
        self.question = (question or "").strip()
        self.options = _as_options(options)
        self.multiple = bool(multiple)
        self.answer: str | None = None
        self.auto = False
        try:
            index = int(recommended)
        except (TypeError, ValueError):
            index = -1
        self.recommended = index if 0 <= index < len(self.options) else -1
        self.why = " ".join((why or "").split())
        self._timeout_s = _as_timeout(timeout_s)
        self._remaining = self._timeout_s
        self._rows: list = []
        self._free: _FreeTextRow | None = None
        self._timer: QTimer | None = None
        self._engaged = False

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_CARD)
        self.head = ask_head(self, self.question, lambda: self.submit(""), self.tr("Skip"))
        col.addWidget(self.head)
        if self.options:
            col.addWidget(self._build_options())
        if allow_free_text or not self.options:
            self._free = _FreeTextRow(self.tr("Something else") if self.options
                                      else self.tr("Type your answer"), self)
            self._free.edit.textEdited.connect(self._on_typed)
            self._free.edit.returnPressed.connect(self._on_return)
            col.addWidget(self._free)
        if self._timeout_s > 0:




            self._timer = QTimer(self)
            self._timer.setInterval(1000)
            self._timer.timeout.connect(self._tick)

    def _build_options(self) -> QWidget:
        host = QWidget(self)
        rows = QVBoxLayout(host)
        rows.setContentsMargins(0, 0, 0, 0)

        rows.setSpacing(2)
        kind = "check" if self.multiple else "radio"
        for i, option in enumerate(self.options):
            row = _OptionRow(option, host, kind, self.why if i == self.recommended else "")
            row.clicked.connect(lambda i=i: self.pick(i))
            rows.addWidget(row)
            self._rows.append(row)
        return host



    def pick(self, index: int) -> None:
        """A row was clicked: select it (toggle it when several may be)."""
        if self.answer is not None or not (0 <= index < len(self._rows)):
            return
        self._engage()
        if self.multiple:
            self._rows[index].set_checked(not self._rows[index].is_checked())
        else:
            for i, row in enumerate(self._rows):
                row.set_checked(i == index)
        if self._free is not None:
            self._free.edit.clear()
        self.changed.emit()

    def _on_typed(self, text: str) -> None:

        self._engage()
        if text:
            for row in self._rows:
                row.set_checked(False)
        self.changed.emit()

    def _engage(self) -> None:
        """The reader is answering: the countdown stops, for good."""





        if self._engaged:
            return
        self._engaged = True
        self._stop_timer()

    def _on_return(self) -> None:
        if self.is_answerable():
            self.submit(self.answer_text())
        elif self.recommended >= 0:
            self.submit(self.options[self.recommended])

    def answer_text(self) -> str:
        """What the page would answer now: the typed text, else the chosen labels joined by a comma."""

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



    def _tick(self) -> None:
        if self.answer is not None:
            self._stop_timer()
            return
        self._remaining = max(0, self._remaining - 1)
        if self._remaining == 0:
            self._stop_timer()
            self._auto_answer()

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def resume_timer(self) -> None:
        """This page is the one on screen: its countdown runs, from where it stopped."""
        if self._engaged:
            return
        if self._timer is not None and self.answer is None and self._remaining > 0:
            self._timer.start()

    def pause_timer(self) -> None:
        """Another page is on screen: this one waits, and keeps the seconds it has left."""
        self._stop_timer()

    def _auto_answer(self) -> None:
        if self.answer is not None or self._engaged:
            return
        index = max(self.recommended, 0)
        answer = self.options[index] if self.options else ""
        self.auto = True

        self.auto_answered.emit(self.tool_call_id, self._timeout_s)
        self.submit(answer)



    def submit(self, answer: str) -> None:
        """The page has its answer; the card hears it once."""
        if self.answer is not None:
            return
        self.settle(answer)
        self.submitted.emit(self.tool_call_id, answer)

    def settle(self, answer: str) -> None:
        """Record an answer without announcing it (the controller already knows: a resolved call, a replayed thread)."""

        if self.answer is not None:
            return
        self.answer = answer or ""
        self._stop_timer()
        self.setEnabled(False)

    def shown_answer(self) -> str:
        shown = self.answer or self.tr("skipped")
        if self.auto:
            shown = f"{shown} · {self.tr('auto')}"
        return shown


class QuestionCard(_Card, FoldMixin):
    """One or several questions, each with a few choices and a text field."""












    answered = pyqtSignal(str, str)
    auto_answered = pyqtSignal(str, int)

    def __init__(self, tool_call_id: str, question: str, options=None,
                 allow_free_text: bool = True, parent=None, recommended: int = -1,
                 why: str = "", timeout_s: int = 0, multiple: bool = False):
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

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACE_OUTER)
        self._stack = QStackedWidget(self._body)
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
                      recommended=recommended, why=why, timeout_s=timeout_s, multiple=multiple)



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
        self._continue_btn = _pill(self._button(self.tr("Continue"), _BTN_PRIMARY_PILL, self._continue))
        footer.set_widgets(self._pager, [skip, self._continue_btn])
        return footer

    def add_page(self, tool_call_id: str, question: str, options=None,
                 allow_free_text: bool = True, recommended: int = -1, why: str = "",
                 timeout_s: int = 0, multiple: bool = False) -> None:
        """The next question of the run, behind the pager."""




        if self._page(tool_call_id) is not None:
            return
        page = _Page(tool_call_id, question, options, allow_free_text, recommended, why,
                     timeout_s, multiple, self._stack)
        page.changed.connect(self._refresh)
        page.submitted.connect(self._on_submitted)
        page.auto_answered.connect(self.auto_answered.emit)
        self._pages.append(page)
        self._stack.addWidget(page)
        self._pager.setVisible(len(self._pages) > 1)
        self._sync_timers()
        self._refresh()



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
        self._current = index
        self._stack.setCurrentIndex(index)
        self._sync_timers()
        self._refresh()

    def _next_open(self) -> int:
        """The first page after the current one still waiting, else the first waiting one anywhere, else -1."""

        order = list(range(self._current + 1, len(self._pages))) + list(range(0, self._current + 1))
        for i in order:
            if self._pages[i].answer is None:
                return i
        return -1

    def _sync_timers(self) -> None:
        """Only the page in front of the user counts down."""
        for i, page in enumerate(self._pages):
            if i == self._current:
                page.resume_timer()
            else:
                page.pause_timer()

    def _refresh(self) -> None:
        """The footer follows the current page: the counter, the chevrons, the verb on the primary and whether it can be pressed."""

        page = self._current_page()
        count = len(self._pages)
        self._counter.setText(f"{self._current + 1}/{count}")
        self._prev_btn.setEnabled(self._current > 0)
        self._next_btn.setEnabled(self._current < count - 1)
        self._next_btn.set_ring(self._current < count - 1 and page is not None
                                and (page.is_answerable() or page.answer is not None))
        last = self._current >= count - 1
        self._continue_btn.setText(self.tr("Send") if last else self.tr("Continue"))
        self._continue_btn.setEnabled(page is not None and page.is_answerable())



    @property
    def answer(self):
        """The first page's answer: None while it waits."""
        return self._pages[0].answer if self._pages else None

    @property
    def auto(self) -> bool:
        return any(page.auto for page in self._pages)

    @property
    def recommended(self) -> int:
        return self._pages[0].recommended if self._pages else -1

    @property
    def why(self) -> str:
        return self._pages[0].why if self._pages else ""

    def is_open(self) -> bool:
        """Whether any page still waits for an answer."""
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

    def keyPressEvent(self, event):  # noqa: N802 - Qt override

        page = self._current_page()
        key = event.key()
        if page is not None and page.answer is None:
            if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
                page.pick(key - Qt.Key.Key_1)
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                page._on_return()
                return
        super().keyPressEvent(event)

    def _continue(self) -> None:
        page = self._current_page()
        if page is not None and page.is_answerable():
            page.submit(page.answer_text())

    def _skip(self) -> None:
        page = self._current_page()
        if page is not None:
            page.submit("")

    def _on_submitted(self, tool_call_id: str, answer: str) -> None:
        self.answered.emit(tool_call_id, answer)
        self._after_answer()

    def _after_answer(self) -> None:
        """Move on to the next waiting page, or fold when none is left."""
        following = self._next_open()
        if following >= 0:
            self._go(following)



            self.focus()
            return
        self._refresh()
        self._fold()

    def collapse(self, answer: str, tool_call_id: str = "") -> None:
        """A page answered elsewhere (the controller, a replay): record it and fold once every page has its answer."""

        page = self._page(tool_call_id) if tool_call_id else self._current_page()
        if page is None:
            return
        page.settle(answer)
        self._after_answer()

    def _fold(self) -> None:



        if self._folding or self._done.isVisible():
            return
        self._folding = True
        for page in self._pages:
            row = done_row(self._done, "check" if page.answer else "dash", qcolor(INK_3),
                           f"{page.question} · {page.shown_answer()}")
            self._done_col.addWidget(row)
        self._body.setEnabled(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fold_body(self._body, self._show_done)

    def stop_asking(self) -> None:
        """The run this card belongs to is over: it no longer decides anything."""





        for page in self._pages:
            page.pause_timer()
            page._engaged = True
        if self.is_open():
            self._body.setEnabled(False)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _show_done(self) -> None:
        self._body.hide()
        self._done.show()
        self.set_frame(_ASK_DONE_QSS)
        self.set_margins(0, 0, 0, 0)
        self.setMaximumWidth(_UNBOUNDED_PX)

    def to_markdown(self) -> str:
        lines = []
        for page in self._pages:
            answer = self.tr("pending") if page.answer is None else (page.answer or self.tr("skipped"))
            if page.auto:
                answer = f"{answer}, {self.tr('auto')}"
            lines.append(f"- **{self.tr('Question')}:** {page.question} ({answer})")
        return "\n".join(lines)
