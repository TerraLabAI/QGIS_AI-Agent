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

from ..core import telemetry
from ..core.memory_store import memory_dir
from ..core.profile import (
    MEMORY_NOTE_MAX_CHARS,
    PROFILE_LINE_MAX_CHARS,
    PROFILE_MAX_CHARS,
    REPLY_LANGUAGES,
    add_memory_note,
    clear_memory_notes,
    load_memory_notes,
    normalize_expertise,
    normalize_question_policy,
    normalize_reply_language,
    normalize_reply_style,
    normalize_units,
    project_key,
    remove_memory_note,
    save_memory_notes,
)
from ..core.qt_compat import qt_deleted
from .cards_click import press_combo_rows
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .mascot_stage import refresh_mascots
from .memory_mascot import ADD_HOVER, ADDED, CLEARED, DELETED, EDITED, EMPTY, OFF, ON, REST, Companion, HoverWatch
from .quota_card import trial_cta
from .settings_pages import (
    GHOST_BTN_QSS,
    INPUT_QSS,
    MUTED,
    PRIMARY_BTN_QSS,
    ROW_NOTE_QSS,
    TEXTAREA_QSS,
    NoteRow,
    Page,
    ProNote,
    SectionCard,
    Segmented,
    SettingGroup,
    SettingRow,
    Switch,
    combo_qss,
    set_section_locked,
)
from .style import WEIGHT_MEDIUM
from .styles import PANEL_TEXT
from .widgets import placeholder_ink

_TEXTAREA_H = 64

_COUNT_SHOWN_FROM = PROFILE_MAX_CHARS - 200
_FIELD_LABEL_QSS = f"font-size: 12px; color: {MUTED}; background: transparent;"


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


    def _build_answers(self, page: Page) -> SettingGroup:



        group = SettingGroup(page)

        self._language_combo = QComboBox(group)
        self._language_combo.setStyleSheet(combo_qss(self))
        self._language_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._language_combo.addItem(self.tr("Same as QGIS"), "")
        for code, native in REPLY_LANGUAGES:
            self._language_combo.addItem(native, code)
        self._language_combo.setMinimumWidth(scale_px_length(180))
        self._language_rows = press_combo_rows(self._language_combo)
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)
        group.add_row(SettingRow(self.tr("Response language"),
                                 "",
                                 self._language_combo, group))

        self._style_segments = Segmented(
            [("concise", self.tr("Concise")), ("balanced", self.tr("Balanced")),
             ("detailed", self.tr("Detailed"))], self._store.reply_style, group)
        self._style_segments.changed.connect(self._on_style_changed)
        group.add_row(SettingRow(self.tr("Response style"),
                                 "",
                                 self._style_segments, group))



        self._units_segments = Segmented(
            [("metric", self.tr("Metric")), ("imperial", self.tr("Imperial"))], self._store.units, group)
        self._units_segments.changed.connect(self._on_units_changed)
        group.add_row(SettingRow(self.tr("Units"),
                                 "",
                                 self._units_segments, group))



        self._questions_segments = Segmented(
            [("minimal", self.tr("Rarely")), ("balanced", self.tr("When needed")),
             ("confirm", self.tr("Often"))], self._store.question_policy, group)
        self._questions_segments.changed.connect(self._on_questions_changed)
        group.add_row(SettingRow(self.tr("Questions"),
                                 self.tr("How often the AI asks before acting."),
                                 self._questions_segments, group))

        self._expertise_segments = Segmented(
            [("", self.tr("Auto")), ("beginner", self.tr("Beginner")),
             ("intermediate", self.tr("Regular")), ("expert", self.tr("Expert"))],
            self._store.expertise, group)
        self._expertise_segments.changed.connect(self._on_expertise_changed)
        group.add_row(SettingRow(self.tr("GIS experience"),
                                 self.tr("How much the AI explains."),
                                 self._expertise_segments, group))




        self._follow_switch = Switch(group, self._store.follow_edits)
        self._follow_switch.toggled.connect(self._on_follow_toggled)
        group.add_row(SettingRow(
            self.tr("Move the map to what it changes"),
            self.tr("The view goes to each edit as it happens. Off keeps your view where you put it."),
            self._follow_switch, group))
        self._mascot_switch = Switch(group, self._store.mascot_animated)
        self._mascot_switch.toggled.connect(self._on_mascot_toggled)
        group.add_row(SettingRow(
            self.tr("Animate the mascot"),
            self.tr("The AI agent's mark moves and follows the work. Off keeps it still."),
            self._mascot_switch, group))
        return group

    def _sync_language_combo(self) -> None:
        code = normalize_reply_language(self._store.reply_language)
        index = max(0, self._language_combo.findData(code))
        self._language_combo.blockSignals(True)
        self._language_combo.setCurrentIndex(index)
        self._language_combo.blockSignals(False)

    def _on_language_changed(self, index: int) -> None:
        code = str(self._language_combo.itemData(index) or "")
        if code == self._store.reply_language:
            return
        self._store.reply_language = code
        telemetry.track_setting("reply_language", code)
        self._show_saved()
        self._emit_profile()

    def _on_style_changed(self, value: str) -> None:
        value = normalize_reply_style(value)
        if value == self._store.reply_style:
            return
        self._store.reply_style = value
        telemetry.track_setting("reply_style", value)
        self._show_saved()
        self._emit_profile()

    def _on_questions_changed(self, value: str) -> None:
        value = normalize_question_policy(value)
        if value == self._store.question_policy:
            return
        self._store.question_policy = value
        telemetry.track_setting("question_policy", value)
        self._show_saved()
        self._emit_profile()

    def _on_follow_toggled(self, on: bool) -> None:
        self._store.follow_edits = bool(on)
        telemetry.track_setting("follow_edits", bool(on))
        self._show_saved()
        self._emit_profile()

    def _on_mascot_toggled(self, on: bool) -> None:
        self._store.mascot_animated = bool(on)
        telemetry.track_setting("mascot_animated", bool(on))
        refresh_mascots()
        for companion in self._memory_companions():
            companion.refresh_mode()
        self._show_saved()

    def _build_personalisation(self) -> Page:

        page = Page(self.tr("Personalisation"),
                    self.tr("Read by the AI agent at the start of every conversation."), self)



        you = SectionCard(page)
        fields = QWidget(you)
        line = QHBoxLayout(fields)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(12)
        self._name_edit = self._field(fields, line, self.tr("Name"), self._store.profile_name,
                                      self.tr("Your first name"))
        self._name_edit.editingFinished.connect(self._on_name_changed)
        self._role_edit = self._field(fields, line, self.tr("Job"), self._store.profile_role,
                                      self.tr("Urban planner"))
        self._role_edit.editingFinished.connect(self._on_role_changed)
        you.add(fields)
        page.add(you)



        locked = self._memory_locked()
        card = you
        self._about_edit, self._about_count = self._text_block(
            card, self.tr("About you"),
            self.tr("Urban planner at the city of Lyon, cadastre and PLU layers in EPSG:2154"),
            self._store.profile_about, "about")
        self._instructions_edit, self._instructions_count = self._text_block(
            card, self.tr("Instructions"),
            self.tr("Always answer in French, never delete a layer without asking"),
            self._store.profile_instructions, "instructions")
        note = self._pro_note(card, self.tr("Pro reads both before every run."), "personalisation")
        boxes = (self._about_edit, self._about_count, self._instructions_edit, self._instructions_count)
        self._plan_sections.append((note, boxes))
        note.setVisible(locked)
        set_section_locked(*boxes, locked=locked)

        page.add_group_title(self.tr("Answers"))
        page.add(self._build_answers(page))
        self._sync_language_combo()
        return page

    def _field(self, host: QWidget, line: QHBoxLayout, label: str, text: str,
               placeholder: str) -> QLineEdit:

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        title = QLabel(label, host)
        title.setStyleSheet(_FIELD_LABEL_QSS)
        title.setContentsMargins(2, 0, 0, 0)
        col.addWidget(title)
        edit = QLineEdit(text, host)
        edit.setStyleSheet(INPUT_QSS)
        edit.setPlaceholderText(placeholder)
        placeholder_ink(edit)
        edit.setMaxLength(PROFILE_LINE_MAX_CHARS)
        col.addWidget(edit)
        line.addLayout(col, 1)
        return edit

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

    def _on_expertise_changed(self, value: str) -> None:
        value = normalize_expertise(value)
        if value == self._store.expertise:
            return
        self._store.expertise = value
        telemetry.track_setting("expertise", value)
        self._show_saved()
        self._emit_profile()

    def _on_units_changed(self, value: str) -> None:
        value = normalize_units(value)
        if value == self._store.units:
            return
        self._store.units = value
        telemetry.track_setting("units", value)
        self._show_saved()
        self._emit_profile()

    def _pro_note(self, host: SectionCard, reason: str, surface: str) -> ProNote:

        note = ProNote(reason)
        note.upgrade_requested.connect(lambda: self._on_upgrade(surface))
        host.add(note)
        self._pro_notes.append(note)
        self._sync_pro_notes()
        return note

    def _sync_pro_notes(self) -> None:

        label = trial_cta() if getattr(self, "trial_offered", False) else ""
        for note in list(getattr(self, "_pro_notes", ())):
            try:
                note.set_trial(label)
            except RuntimeError:
                continue

    def _text_block(self, card: SectionCard, title: str, placeholder: str, text: str, key: str):

        box = QWidget(card)
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        label = QLabel(title, box)
        label.setStyleSheet(_FIELD_LABEL_QSS)
        label.setContentsMargins(2, 0, 0, 0)
        col.addWidget(label)
        edit = QPlainTextEdit(box)
        edit.setStyleSheet(TEXTAREA_QSS)
        edit.setPlaceholderText(placeholder)
        placeholder_ink(edit)
        edit.setPlainText(text or "")
        edit.setFixedHeight(scale_px_length(_TEXTAREA_H))
        edit.setTabChangesFocus(True)
        col.addWidget(edit)
        count = QLabel(box)
        count.setStyleSheet(ROW_NOTE_QSS)
        count.setAlignment(Qt.AlignmentFlag.AlignRight)
        count.setContentsMargins(0, 0, 4, 0)
        col.addWidget(count)
        self._update_count(edit, count)
        edit.textChanged.connect(lambda: self._on_profile_text_changed(edit, count, key))
        card.add(box)
        return edit, count

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

    def _sync_from_store(self) -> None:

        self._sync_language_combo()
        self._style_segments.set_value(self._store.reply_style)
        self._questions_segments.set_value(self._store.question_policy)
        self._expertise_segments.set_value(self._store.expertise)
        self._units_segments.set_value(self._store.units)
        self._values["send_shortcut"] = self._store.send_shortcut
        for edit, value in ((self._name_edit, self._store.profile_name),
                            (self._role_edit, self._store.profile_role)):
            edit.blockSignals(True)
            edit.setText(value)
            edit.blockSignals(False)
        for switch, value in ((self._follow_switch, self._store.follow_edits),
                              (self._memory_switch, self._store.memory_enabled)):
            switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(False)
        for edit, text in ((self._about_edit, self._store.profile_about),
                           (self._instructions_edit, self._store.profile_instructions)):
            edit.blockSignals(True)
            edit.setPlainText(text)
            edit.blockSignals(False)
        self._update_count(self._about_edit, self._about_count)
        self._update_count(self._instructions_edit, self._instructions_count)
        self._fill_notes()
