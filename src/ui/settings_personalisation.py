# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import os

from qgis.PyQt.QtCore import QDateTime, QLocale, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import notify_sound, telemetry
from ..core.i18n import UI_LANGUAGES
from ..core.memory_store import memory_dir
from ..core.profile import (
    MEMORY_NOTE_MAX_CHARS,
    PROFILE_LINE_MAX_CHARS,
    PROFILE_MAX_CHARS,
    add_memory_note,
    clear_memory_notes,
    load_memory_notes,
    normalize_reply_style,
    project_key,
    remove_memory_note,
    save_memory_notes,
)
from ..core.qt_compat import qt_deleted
from .cards_click import press_combo_rows
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .memory_mascot import ADD_HOVER, ADDED, CLEARED, DELETED, EDITED, EMPTY, OFF, ON, REST, Companion, HoverWatch
from .settings_pages import (
    GHOST_BTN_QSS,
    INPUT_QSS,
    MUTED,
    PRIMARY_BTN_QSS,
    ROW_NOTE_QSS,
    NoteRow,
    Page,
    SectionCard,
    Segmented,
    SettingGroup,
    SettingRow,
    Switch,
    combo_qss,
)
from .style import WEIGHT_MEDIUM
from .styles import PANEL_TEXT
from .widgets import IconButton, placeholder_ink


_COUNT_SHOWN_FROM = PROFILE_MAX_CHARS - 200


_MEMORY_MARK_PX = 26
_MEMORY_EMPTY_PX, _MEMORY_EMPTY_FLOOR = 56, 92


class _NoteField(QLineEdit):


    escaped = pyqtSignal()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class PersonalisationPageMixin:


    def _build_answers(self, page: Page) -> dict:




        group = SettingGroup(page)
        rows = []

        self._language_combo = QComboBox(group)
        self._language_combo.setStyleSheet(combo_qss(self))
        self._language_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._language_combo.addItem(self.tr("Same as QGIS"), "")
        for code, native in UI_LANGUAGES:
            self._language_combo.addItem(native, code)
        self._language_combo.setMinimumWidth(scale_px_length(180))
        self._language_rows = press_combo_rows(self._language_combo)
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)

        language = SettingRow(self.tr("Language"), self.tr("Applies after restarting QGIS."),
                              self._language_combo, group)
        language.setProperty("keepNote", True)
        rows.append(("language", language))

        self._style_segments = Segmented(
            [("concise", self.tr("Concise")), ("balanced", self.tr("Balanced")),
             ("detailed", self.tr("Detailed"))], self._store.reply_style, group)
        self._style_segments.changed.connect(self._on_style_changed)
        rows.append(("style", SettingRow(self.tr("Response style"), "", self._style_segments, group)))

        group.hide()
        return dict(rows)

    def _sync_language_combo(self) -> None:
        index = max(0, self._language_combo.findData(self._store.ui_language))
        self._language_combo.blockSignals(True)
        self._language_combo.setCurrentIndex(index)
        self._language_combo.blockSignals(False)

    def _on_language_changed(self, index: int) -> None:
        code = str(self._language_combo.itemData(index) or "")
        if code == self._store.ui_language:
            return
        self._store.ui_language = code
        telemetry.track_setting("ui_language", code)
        self._show_saved()

    def _on_style_changed(self, value: str) -> None:
        value = normalize_reply_style(value)
        if value == self._store.reply_style:
            return
        self._store.reply_style = value
        telemetry.track_setting("reply_style", value)
        self._show_saved()
        self._emit_profile()

    def _sound_names(self) -> dict:
        return {"chime": self.tr("Chime"), "marimba": self.tr("Marimba"),
                "bell": self.tr("Bell"), "train": self.tr("Little train")}

    def _sound_picker(self, host: QWidget, key: str) -> QWidget:

        names = self._sound_names()
        picker = QWidget(host)
        line = QHBoxLayout(picker)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        combo = QComboBox(picker)
        combo.setStyleSheet(combo_qss(self))
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for sound in notify_sound.SOUNDS:
            combo.addItem(names.get(sound, sound), sound)
        combo.setCurrentIndex(max(0, combo.findData(getattr(self._store, key))))
        combo.setMinimumWidth(scale_px_length(130))
        picker.combo_rows = press_combo_rows(combo)
        combo.currentIndexChanged.connect(lambda _i, k=key, c=combo: self._on_sound_picked(k, c))
        line.addWidget(combo)
        play = IconButton(picker, "lu.play", 14, self.tr("Play"))
        play.clicked.connect(lambda _=False, c=combo: self._preview_sound(str(c.currentData())))
        line.addWidget(play)
        self._sound_rows.append(picker)
        return picker

    def _preview_sound(self, sound: str) -> None:
        notify_sound.play(sound, force=True)

    def _on_sound_picked(self, key: str, combo: QComboBox) -> None:
        sound = str(combo.currentData())
        setattr(self._store, key, sound)
        telemetry.track_setting(key, sound)
        self._preview_sound(sound)
        self._sync_sounds_summary()
        self._show_saved()

    def _on_suggest_switch(self, on: bool) -> None:
        self._store.suggestions_enabled = bool(on)
        telemetry.track_setting("suggestions_enabled", bool(on))
        self._show_saved()

    def _on_notify_mode_changed(self, value: str) -> None:
        if value == self._store.notify_mode:
            return
        self._store.notify_mode = value
        telemetry.track_setting("notify_mode", value)
        self._sync_notify_rows()
        self._show_saved()

    def _sync_notify_rows(self) -> None:
        on = self._store.notify_mode == "sound"
        for widget in self._sound_rows:
            widget.setEnabled(on)

    def _on_name_changed(self) -> None:
        value = " ".join(self._name_edit.text().split())[:PROFILE_LINE_MAX_CHARS]
        if self._name_edit.text() != value:
            self._name_edit.setText(value)
        if value == self._store.profile_name:
            return
        self._store.profile_name = value
        self._show_saved()
        self._emit_profile()

    def _on_role_changed(self) -> None:
        value = " ".join(self._role_edit.text().split())[:PROFILE_LINE_MAX_CHARS]
        if self._role_edit.text() != value:
            self._role_edit.setText(value)
        if value == self._store.profile_role:
            return
        self._store.profile_role = value
        self._show_saved()
        self._emit_profile()

    def _update_count(self, edit: QPlainTextEdit, count: QLabel) -> None:
        used = len(edit.toPlainText())
        count.setText(self.tr("{count} / {limit}").format(count=used, limit=PROFILE_MAX_CHARS))
        count.setVisible(used >= _COUNT_SHOWN_FROM)

    def _on_profile_text_changed(self, edit: QPlainTextEdit, count: QLabel, key: str) -> None:
        text = edit.toPlainText()
        if len(text) > PROFILE_MAX_CHARS:
            edit.blockSignals(True)
            edit.setPlainText(text[:PROFILE_MAX_CHARS])
            cursor = edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            edit.setTextCursor(cursor)
            edit.blockSignals(False)
        self._update_count(edit, count)
        self._save_later(key, lambda: self._save_profile_text(edit, key))

    def _save_profile_text(self, edit: QPlainTextEdit, key: str) -> None:
        try:
            text = edit.toPlainText()[:PROFILE_MAX_CHARS]
        except RuntimeError:
            return
        if key == "about":
            if text == self._store.profile_about:
                return
            self._store.profile_about = text
        else:
            if text == self._store.profile_instructions:
                return
            self._store.profile_instructions = text
        self._show_saved()
        self._emit_profile()

    def _build_memory_page(self) -> Page:





        page = Page(self.tr("Memory"),
                    self.tr("What the AI keeps between conversations, stored on this computer."), self)
        card = SectionCard(page)


        card.layout().setContentsMargins(0, 0, 0, 8)
        page.add(card)

        group = SettingGroup(card, flat=True)
        self._memory_switch = Switch(group, self._store.memory_enabled)
        self._memory_switch.toggled.connect(self._on_memory_toggled)
        group.add_row(SettingRow(self.tr("The AI saves notes on its own"),
                                 self.tr("The rules you give it and the corrections that hold for later."),
                                 self._memory_switch, group))
        card.add(group)



        self._memory_mark = Companion(page, self._store, _MEMORY_MARK_PX, beside=page.title_label)
        self._memory_empty = None


        self._notes_host = QWidget(card)
        notes_col = QVBoxLayout(self._notes_host)
        notes_col.setContentsMargins(0, 0, 0, 0)
        notes_col.setSpacing(0)
        card.add(self._notes_host)

        add_host = QWidget(card)
        add_col = QVBoxLayout(add_host)
        add_col.setContentsMargins(12, 0, 12, 0)
        add_col.setSpacing(6)
        self._add_note_btn = QPushButton(self.tr("+ Add a note"), add_host)
        self._add_note_btn.setObjectName("memoryAddNote")
        self._add_note_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {MUTED};"
            f" font-size: 12px; font-weight: {WEIGHT_MEDIUM}; padding: 4px 2px; text-align: left; }}"
            " QPushButton:hover { color: " + PANEL_TEXT + "; }")
        self._add_note_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_note_btn.setAutoDefault(False)
        self._add_note_btn.clicked.connect(self._open_add_note)
        HoverWatch(self._add_note_btn, lambda: self._memory_moment(ADD_HOVER, self._add_note_btn),
                   lambda: [c.mascot.hover(None) for c in self._memory_companions()])
        note_actions = QHBoxLayout()
        note_actions.setContentsMargins(0, 0, 0, 0)
        note_actions.addWidget(self._add_note_btn, 0, Qt.AlignmentFlag.AlignLeft)
        note_actions.addStretch(1)
        self._clear_notes_btn = QPushButton(self.tr("Delete all notes"), add_host)
        self._clear_notes_btn.setObjectName("memoryClearNotes")
        self._clear_notes_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {MUTED};"
            " font-size: 12px; padding: 4px 2px; } QPushButton:hover { color: " + PANEL_TEXT + "; }")
        self._clear_notes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_notes_btn.setAutoDefault(False)
        self._clear_notes_btn.clicked.connect(self._on_clear_notes)
        note_actions.addWidget(self._clear_notes_btn, 0, Qt.AlignmentFlag.AlignRight)
        add_col.addLayout(note_actions)
        self._add_row = QWidget(add_host)
        add_lay = QHBoxLayout(self._add_row)
        add_lay.setContentsMargins(0, 0, 0, 0)
        add_lay.setSpacing(8)
        self._note_edit = _NoteField(self._add_row)
        self._note_edit.setStyleSheet(INPUT_QSS)
        self._note_edit.setPlaceholderText(self.tr("e.g. I work in EPSG:2154"))
        placeholder_ink(self._note_edit)
        self._note_edit.setMaxLength(MEMORY_NOTE_MAX_CHARS)
        self._note_edit.returnPressed.connect(self._on_add_note)
        self._note_edit.escaped.connect(self._close_add_note)
        add_lay.addWidget(self._note_edit, 1)
        cancel_btn = QPushButton(self.tr("Cancel"), self._add_row)
        cancel_btn.setStyleSheet(GHOST_BTN_QSS)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self._close_add_note)
        add_lay.addWidget(cancel_btn, 0)
        add_btn = QPushButton(self.tr("Add"), self._add_row)
        add_btn.setStyleSheet(PRIMARY_BTN_QSS)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setAutoDefault(False)
        add_btn.clicked.connect(self._on_add_note)
        add_lay.addWidget(add_btn, 0)
        self._add_row.hide()
        add_col.addWidget(self._add_row)
        card.add(add_host)



        folder = QWidget(card)
        folder_lay = QHBoxLayout(folder)
        folder_lay.setContentsMargins(14, 4, 14, 0)
        folder_lay.setSpacing(4)
        folder_text = QLabel(self.tr("Markdown files on this computer"), folder)
        folder_text.setStyleSheet(ROW_NOTE_QSS)
        folder_lay.addWidget(folder_text)
        dot = QLabel("·", folder)
        dot.setStyleSheet(ROW_NOTE_QSS)
        folder_lay.addWidget(dot)
        folder_btn = QPushButton(self.tr("Open"), folder)
        folder_btn.setObjectName("memoryFolderOpen")
        folder_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {MUTED}; font-size: 11px;"
            " text-decoration: underline; padding: 0; } QPushButton:hover { color: " + PANEL_TEXT + "; }")
        folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_btn.setAutoDefault(False)
        folder_btn.clicked.connect(self._on_open_memory_folder)
        folder_btn.setEnabled(bool(memory_dir()))
        folder_lay.addWidget(folder_btn)
        folder_lay.addStretch(1)
        card.add(folder)
        self._fill_notes()
        return page

    def _memory_companions(self) -> list:

        found = [getattr(self, "_memory_mark", None), getattr(self, "_memory_empty", None)]
        return [c for c in found if not qt_deleted(c) and not qt_deleted(c.mascot)]

    def _memory_moment(self, event: str = "", look=None) -> None:


        empty = not load_memory_notes(self._store)
        mood = OFF if not self._store.memory_enabled else EMPTY if empty else REST
        for companion in self._memory_companions():
            companion.play(mood)
            if event:
                companion.play(event, look)

    def _open_add_note(self) -> None:
        self._add_note_btn.hide()
        self._add_row.show()
        self._note_edit.setFocus()

    def _close_add_note(self) -> None:
        self._note_edit.clear()
        self._add_row.hide()
        self._add_note_btn.show()

    def _fill_notes(self) -> None:


        column = self._notes_host.layout()
        while column.count():
            widget = column.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        notes = load_memory_notes(self._store)
        self._clear_notes_btn.setVisible(bool(notes))
        self._memory_empty = None
        self._memory_mark.mascot.setVisible(bool(notes))
        here = self._open_project_key()
        pinned = [n for n in notes if n["scope"] == "project"]
        places = (
            (self.tr("This project · {n}"), [n for n in pinned if here and n["project"] == here]),
            (self.tr("Everywhere · {n}"), [n for n in notes if n["scope"] != "project"]),
            (self.tr("Other projects · {n}"), [n for n in pinned if not (here and n["project"] == here)]),
        )
        if not notes:
            floor = QWidget(self._notes_host)
            floor.setFixedHeight(scale_px_length(_MEMORY_EMPTY_FLOOR))
            column.addWidget(floor)
            self._memory_empty = Companion(floor, self._store, _MEMORY_EMPTY_PX)
            empty = QLabel(self.tr("No notes yet."), self._notes_host)
            empty.setStyleSheet(ROW_NOTE_QSS)
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            empty.setContentsMargins(14, 4, 14, 12)
            column.addWidget(empty)
            self._memory_moment()
            return
        for heading, found in places:
            if not found:
                continue
            label = QLabel(heading.format(n=len(found)), self._notes_host)
            label.setStyleSheet(
                f"font-size: 11px; font-weight: {WEIGHT_MEDIUM}; color: {MUTED}; background: transparent;")
            label.setContentsMargins(14, 8, 14, 0)
            column.addWidget(label)
            group = SettingGroup(self._notes_host, flat=True)
            for note in reversed(found):
                row = NoteRow(note["text"], self._note_caption(note), group, note["id"])
                row.removed.connect(self._on_remove_note)
                row.edited.connect(self._on_edit_note)
                group.add_row(row)
            column.addWidget(group)
        apply_font_scale_to_tree(self._notes_host)
        self._memory_moment()

    def _open_project_key(self) -> str:

        try:
            from qgis.core import QgsProject
            return project_key(QgsProject.instance().fileName())
        except Exception:  # noqa: BLE001
            return ""

    def _note_caption(self, note: dict) -> str:


        parts = [self.tr("Added by the AI") if note.get("source") == "ai" else self.tr("Added by you")]
        stamp = str(note.get("updated_at") or note.get("created_at") or "")
        when = QDateTime.fromString(stamp, Qt.DateFormat.ISODate)
        if when.isValid():
            parts.append(QLocale().toString(when.toLocalTime().date(), QLocale.FormatType.ShortFormat))
        return " · ".join(parts)

    def _on_add_note(self) -> None:
        text = self._note_edit.text().strip()
        if not text:
            return
        if add_memory_note(self._store, text, "user") is None:
            self._note_edit.selectAll()
            return
        telemetry.track_setting("memory_note", "added")
        self._close_add_note()
        self._fill_notes()
        self._memory_moment(ADDED)
        self._show_saved()
        self._emit_profile()

    def _on_remove_note(self, text: str) -> None:
        if remove_memory_note(self._store, text):
            telemetry.track_setting("memory_note", "deleted")
            self._fill_notes()
            self._memory_moment(DELETED)
            self._show_saved()
            self._emit_profile()

    def _on_clear_notes(self) -> None:

        count = len(load_memory_notes(self._store))
        if not count:
            return
        answer = QMessageBox.question(
            self, self.tr("Delete all notes"),
            self.tr("Delete this note? The AI will not add it back.") if count == 1
            else self.tr("Delete all {n} notes? The AI will not add them back.").format(n=count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        clear_memory_notes(self._store)
        telemetry.track_setting("memory_note", "cleared")
        self._fill_notes()
        self._memory_moment(CLEARED)
        self._show_saved()
        self._emit_profile()

    def _on_edit_note(self, note_id: str, text: str) -> None:







        note = next((n for n in load_memory_notes(self._store) if n["id"] == note_id), None)
        if note is None:
            return
        if add_memory_note(self._store, text, "user", note["kind"], note["scope"],
                           note["project"], note_id) is None:
            return
        telemetry.track_setting("memory_note", "edited")
        self._fill_notes()
        self._memory_moment(EDITED)
        self._show_saved()
        self._emit_profile()

    def _on_open_memory_folder(self) -> None:






        save_memory_notes(self._store, load_memory_notes(self._store))
        path = memory_dir()
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_memory_toggled(self, on: bool) -> None:
        self._store.memory_enabled = bool(on)
        telemetry.track_setting("memory_enabled", bool(on))
        self._memory_moment(ON if on else "")
        self._show_saved()
        self._emit_profile()
