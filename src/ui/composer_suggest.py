# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

import re

from qgis.PyQt.QtCore import QEvent, QObject, Qt

from ..core import security, telemetry
from ..core import telemetry_events as ev


def _goes_on_from(suggestion: str, typed: str) -> bool:
    return bool(typed) and suggestion.casefold().startswith(typed.casefold()) and len(suggestion) > len(typed)


class ComposerSuggest(QObject):


    def __init__(self, composer, text_box):
        super().__init__(composer)
        self._composer = composer
        self._box = text_box
        self._suggester = None
        self._rows: list[tuple[str, str]] = []
        self._predictions: dict[str, tuple[str, str]] = {}
        self._counted = ""
        self._word_taken = ""
        self._writing = False
        text_box.installEventFilter(self)
        text_box.textChanged.connect(self._on_text)
        text_box.cursorPositionChanged.connect(self._paint)

    def attach(self, suggester) -> None:
        self._suggester = suggester
        suggester.ready.connect(self._on_ready)
        suggester.predicted.connect(self._on_predicted)



    def _typed(self) -> str:
        return self._box.text().lstrip()

    def _quiet(self) -> bool:
        cursor = self._box.textCursor()
        return (self._composer.suggest_quiet() or self._box.sheet().isVisible() or bool(self._box.mentions())
                or cursor.hasSelection() or not cursor.atEnd())

    @property
    def _prediction(self) -> str:
        key = self._composer.thread_key()
        return self._predictions.get(key, ("", ""))[1] if key else ""

    def _drop_prediction(self) -> None:
        self._predictions.pop(self._composer.thread_key() or "", None)

    def _suggestion(self) -> tuple[str, str, str]:

        if self._quiet():
            return "", "", ""
        typed, prediction = self._typed(), self._prediction
        if prediction and (not typed or _goes_on_from(prediction, typed)):
            return "next", "", prediction
        intent, text = next((row for row in self._rows if _goes_on_from(row[1], typed)), ("", ""))
        return ("typing", intent, text) if text else ("", "", "")

    def predicting(self) -> bool:
        return bool(self._prediction) and not self._typed() and not self._quiet()

    def refresh(self) -> None:

        self._rows = []
        self._paint()
        self.prepare()

    def prepare(self, focused: bool | None = None) -> None:

        if focused is None:
            focused = self._box.hasFocus()
        if self._suggester is not None and focused and not self._typed() and not self._quiet():
            self._suggester.prepare(self._composer.thread_key())

    def forget_rows(self) -> None:

        self._rows = []

    def _paint(self) -> None:
        kind, intent, whole = self._suggestion()
        self._box.set_ghost(whole[len(self._typed()):] if whole else "")
        self._composer.suggestion_drawn()
        if whole and whole != self._counted:
            self._counted = whole
            self._track(ev.AGENT_SUGGESTION_SHOWN, kind, intent)

    def _run_id(self) -> str:
        return self._predictions.get(self._composer.thread_key() or "", ("", ""))[0]

    def _track(self, event: str, kind: str, intent: str, run_id: str = "", **extra) -> None:
        props = {"kind": kind, **extra}
        if intent:
            props["intent"] = intent
        if kind == "next" and (run_id or self._run_id()):
            props["run_id"] = run_id or self._run_id()
        telemetry.track(event, props)

    def _on_text(self) -> None:
        if self._writing or self._suggester is None:
            return
        typed = self._typed()
        if typed and not _goes_on_from(self._prediction, typed):
            self._drop_prediction()
        self._paint()
        self._suggester.typed(typed, quiet=self._quiet(), thread_id=self._composer.thread_key())

    def _on_ready(self, items, asked: str) -> None:
        typed = self._typed()
        if not typed.casefold().startswith(asked.strip().casefold()):
            return
        rows = [(str(i.get("intent") or ""), str(i.get("text") or "")) for i in items or [] if isinstance(i, dict)]
        rows = [row for row in rows if row[1]]
        if asked:

            self._rows = rows + [row for row in self._rows if row not in rows and _goes_on_from(row[1], typed)]
        elif typed:
            self._rows = self._rows + [row for row in rows if row not in self._rows]
        else:
            self._rows = rows
        self._paint()

    def _on_predicted(self, thread_id: str, run_id: str, text: str) -> None:
        here = thread_id == self._composer.thread_key()
        if here and (self._typed() or self._composer.is_running()):
            return
        self._predictions[thread_id] = (run_id, text)
        if here:
            self._paint()

    def forget_prediction(self) -> None:
        if self._prediction:
            self._drop_prediction()
            self._paint()



    def _take(self, key: str) -> None:
        kind, intent, text = self._suggestion()
        if not text:
            return
        self._rows = []
        typed = self._typed()
        run_id = self._run_id()
        self._drop_prediction()
        security.note_model_text(text, typed)
        self._writing = True
        try:
            self._box.set_text(text)
        finally:
            self._writing = False
        self._paint()
        self._track(ev.AGENT_SUGGESTION_TAKEN, kind, intent, run_id, key=key)
        if self._suggester is not None:
            self._suggester.taken()
            self._suggester.typed(text, quiet=True)

    def _take_word(self) -> None:

        kind, intent, whole = self._suggestion()
        typed = self._box.text()
        rest = whole[len(self._typed()):]
        word = re.match(r"\s*\S+", rest)
        if not whole or word is None:
            return
        security.note_model_text(typed + word.group(), typed)
        self._writing = True
        try:
            self._box.set_text(typed + word.group())
        finally:
            self._writing = False
        self._paint()
        if whole != self._word_taken:
            self._word_taken = whole
            self._track(ev.AGENT_SUGGESTION_TAKEN, kind, intent, key="word")

    def _dismiss(self) -> None:
        kind, intent, _text = self._suggestion()
        self._rows = []
        run_id = self._run_id()
        self._drop_prediction()
        self._paint()
        self._track(ev.AGENT_SUGGESTION_DISMISSED, kind, intent, run_id)
        if kind == "typing" and self._suggester is not None:
            self._suggester.dismissed()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._box and event.type() == QEvent.Type.FocusIn:
            self.prepare(focused=True)
        if watched is not self._box or not self._box.ghost():
            return False
        kind = event.type()
        if kind not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        key, modifiers = event.key(), event.modifiers()
        by_word = key == Qt.Key.Key_Right and modifiers in (Qt.KeyboardModifier.ControlModifier,
                                                            Qt.KeyboardModifier.AltModifier)
        if not by_word and modifiers not in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier):
            return False
        if key not in (Qt.Key.Key_Tab, Qt.Key.Key_Right, Qt.Key.Key_Escape):
            return False
        if kind == QEvent.Type.ShortcutOverride:

            event.accept()
            return True
        if by_word:
            self._take_word()
        elif key == Qt.Key.Key_Escape:
            self._dismiss()
        else:
            self._take("tab" if key == Qt.Key.Key_Tab else "right")
        return True

    def shutdown(self) -> None:
        if self._suggester is not None:
            pairs = ((self._suggester.ready, self._on_ready), (self._suggester.predicted, self._on_predicted))
            for signal, slot in pairs:
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            self._suggester = None
