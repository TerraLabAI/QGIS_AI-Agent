# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Personalisation and Memory: two of the pages `SettingsDialog` builds."""






from __future__ import annotations

import os

from qgis.PyQt.QtCore import QDateTime, QLocale, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from ..core.memory_store import memory_dir
from ..core.profile import (
    MEMORY_NOTE_MAX_CHARS,
    PROFILE_LINE_MAX_CHARS,
    PROFILE_MAX_CHARS,
    REPLY_LANGUAGES,
    add_memory_note,
    load_memory_notes,
    normalize_expertise,
    normalize_layer_naming,
    normalize_question_policy,
    normalize_reply_language,
    normalize_reply_style,
    normalize_units,
    project_key,
    remove_memory_note,
    save_memory_notes,
)
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .settings_pages import (
    GHOST_BTN_QSS,
    INPUT_QSS,
    ROW_NOTE_QSS,
    TEXTAREA_QSS,
    NoteRow,
    Page,
    ProCard,
    SectionCard,
    Segmented,
    SettingGroup,
    SettingRow,
    Switch,
    combo_qss,
    set_section_locked,
)


_TEXTAREA_H = 118


class PersonalisationPageMixin:
    """Personalisation (who you are, how you work, Answers) and Memory."""



    def _build_answers(self, page: Page) -> SettingGroup:
        """How the AI writes back: the language, the length, how often it asks."""




        group = SettingGroup(page)

        self._language_combo = QComboBox(group)
        self._language_combo.setStyleSheet(combo_qss(self))
        self._language_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._language_combo.addItem(self.tr("Same as QGIS"), "")
        for code, native in REPLY_LANGUAGES:
            self._language_combo.addItem(native, code)
        self._language_combo.setMinimumWidth(scale_px_length(180))
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)
        group.add_row(SettingRow(self.tr("Response language"),
                                 self.tr("The language the AI writes its answers in."),
                                 self._language_combo, group))

        self._style_segments = Segmented(
            [("concise", self.tr("Concise")), ("balanced", self.tr("Balanced")),
             ("detailed", self.tr("Detailed"))], self._store.reply_style, group)
        self._style_segments.changed.connect(self._on_style_changed)
        group.add_row(SettingRow(self.tr("Response style"),
                                 self.tr("Short answers, a balance, or the full reasoning."),
                                 self._style_segments, group))





        self._questions_segments = Segmented(
            [("minimal", self.tr("Rarely")), ("balanced", self.tr("When needed")),
             ("confirm", self.tr("Often"))], self._store.question_policy, group)
        self._questions_segments.changed.connect(self._on_questions_changed)
        group.add_row(SettingRow(self.tr("Questions"),
                                 self.tr("How often the AI asks before acting."),
                                 self._questions_segments, group))

        self._timeout_combo = QComboBox(group)
        self._timeout_combo.setStyleSheet(combo_qss(self))
        self._timeout_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for seconds, name in self._timeout_texts():
            self._timeout_combo.addItem(name, seconds)
        self._timeout_combo.setMinimumWidth(scale_px_length(180))
        self._timeout_combo.currentIndexChanged.connect(self._on_timeout_picked)
        group.add_row(SettingRow(
            self.tr("Answer a question for me after"),
            self.tr("The AI takes the option it recommended and carries on."),
            self._timeout_combo, group))

        self._explain_switch = Switch(group, self._store.explain_runs)
        self._explain_switch.toggled.connect(self._on_explain_toggled)
        group.add_row(SettingRow(self.tr("Explain what it did after each run"),
                                 self.tr("A short summary of the changes once a task is done."),
                                 self._explain_switch, group))

        self._tools_switch = Switch(group, self._store.show_tool_details)
        self._tools_switch.toggled.connect(self._on_tools_toggled)
        group.add_row(SettingRow(self.tr("Show tool details in the trace"),
                                 self.tr("Each step with its inputs and results, under the answer."),
                                 self._tools_switch, group))




        self._follow_switch = Switch(group, self._store.follow_edits)
        self._follow_switch.toggled.connect(self._on_follow_toggled)
        group.add_row(SettingRow(
            self.tr("Move the map to what it changes"),
            self.tr("The view goes to each edit as it happens. Off keeps your view where you put it."),
            self._follow_switch, group))
        return group

    def _timeout_texts(self) -> tuple:
        """``(seconds, label)`` for the delay before a question answers itself."""
        return (
            (0, self.tr("Always wait for me")),
            (30, self.tr("30 seconds")),
            (60, self.tr("1 minute")),
            (120, self.tr("2 minutes")),
        )

    def _sync_timeout_combo(self) -> None:
        current = self._store.question_timeout_s
        index = self._timeout_combo.findData(current)
        if index < 0:
            index = max(0, self._timeout_combo.findData(0))
        self._timeout_combo.blockSignals(True)
        self._timeout_combo.setCurrentIndex(index)
        self._timeout_combo.blockSignals(False)

    def _on_timeout_picked(self, index: int) -> None:
        seconds = self._timeout_combo.itemData(index)
        if not isinstance(seconds, int) or seconds == self._store.question_timeout_s:
            return
        self._store.question_timeout_s = seconds
        self._show_saved()

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
        self._show_saved()
        self._emit_profile()

    def _on_style_changed(self, value: str) -> None:
        value = normalize_reply_style(value)
        if value == self._store.reply_style:
            return
        self._store.reply_style = value
        self._show_saved()
        self._emit_profile()

    def _on_questions_changed(self, value: str) -> None:
        value = normalize_question_policy(value)
        if value == self._store.question_policy:
            return
        self._store.question_policy = value
        self._show_saved()
        self._emit_profile()

    def _on_explain_toggled(self, on: bool) -> None:
        self._store.explain_runs = bool(on)
        self._show_saved()
        self._emit_profile()

    def _on_tools_toggled(self, on: bool) -> None:
        self._store.show_tool_details = bool(on)
        self._show_saved()
        self._emit_profile()

    def _on_follow_toggled(self, on: bool) -> None:
        self._store.follow_edits = bool(on)
        self._show_saved()
        self._emit_profile()



    def _build_personalisation(self) -> Page:
        """Everything the AI knows about you before you type: the profile, how you work, and the notes it keeps between conversations."""







        page = Page(self.tr("Personalisation"),
                    self.tr("Context the AI reads at the start of every conversation, "
                            "and the notes it keeps between them."), self)





        page.add_group_title(self.tr("Who you are"))
        identity = SettingGroup(page)
        self._name_edit = QLineEdit(self._store.profile_name, identity)
        self._name_edit.setStyleSheet(INPUT_QSS)
        self._name_edit.setPlaceholderText(self.tr("Your first name"))
        self._name_edit.setMaxLength(PROFILE_LINE_MAX_CHARS)
        self._name_edit.setFixedWidth(scale_px_length(200))
        self._name_edit.editingFinished.connect(self._on_name_changed)
        identity.add_row(SettingRow(self.tr("What the AI should call you"),
                                    self.tr("Used in its answers. Empty: it uses no name."),
                                    self._name_edit, identity))
        self._role_edit = QLineEdit(self._store.profile_role, identity)
        self._role_edit.setStyleSheet(INPUT_QSS)
        self._role_edit.setPlaceholderText(self.tr("Urban planner"))
        self._role_edit.setMaxLength(PROFILE_LINE_MAX_CHARS)
        self._role_edit.setFixedWidth(scale_px_length(200))
        self._role_edit.editingFinished.connect(self._on_role_changed)
        identity.add_row(SettingRow(self.tr("What you do"),
                                    self.tr("Your job in a few words. It changes which data and "
                                            "which method it reaches for first."),
                                    self._role_edit, identity))
        page.add(identity)
        locked = self._memory_locked()
        self._about_edit, self._about_count = self._text_block(
            page, self.tr("About you"),
            self.tr("Who you are and what you work on. Example: urban planner at the city of Lyon, "
                    "I mostly work with cadastre and PLU layers in EPSG:2154."),
            self._store.profile_about, "about", pro_only=locked,
            pro_note=self.tr("Pro reads this before every run, so your job, your city and your usual "
                             "CRS do not have to be typed into each prompt."))
        self._instructions_edit, self._instructions_count = self._text_block(
            page, self.tr("Instructions for the AI"),
            self.tr("How it should work. Example: always answer in French, name new layers in "
                    "snake_case, never delete a layer without asking."),
            self._store.profile_instructions, "instructions", pro_only=locked,
            pro_note=self.tr("Pro follows your standing rules in every run: the language it answers in, "
                             "how it names layers, and what it must never do without asking."))
        page.add_group_title(self.tr("How you work"))
        page.add(self._build_work_preferences(page))



        page.add_group_title(self.tr("Answers"))
        page.add(self._build_answers(page))
        self._sync_language_combo()
        self._sync_timeout_combo()
        self._build_memory(page)
        return page

    def _build_work_preferences(self, page: Page) -> SettingGroup:
        group = SettingGroup(page)
        self._expertise_segments = Segmented(
            [("", self.tr("Auto")), ("beginner", self.tr("Beginner")),
             ("intermediate", self.tr("Regular")), ("expert", self.tr("Expert"))],
            self._store.expertise, group)
        self._expertise_segments.changed.connect(self._on_expertise_changed)
        group.add_row(SettingRow(self.tr("GIS experience"),
                                 self.tr("How much the AI explains."),
                                 self._expertise_segments, group))








        self._units_segments = Segmented(
            [("metric", self.tr("Metric")), ("imperial", self.tr("Imperial"))], self._store.units, group)
        self._units_segments.changed.connect(self._on_units_changed)
        group.add_row(SettingRow(self.tr("Units"),
                                 self.tr("Metres and hectares, or feet, miles and acres."),
                                 self._units_segments, group))

        self._naming_segments = Segmented(
            [("human", self.tr("Plain words")), ("snake_case", self.tr("Computer-style names"))],
            self._store.layer_naming, group)
        self._naming_segments.changed.connect(self._on_naming_changed)
        group.add_row(SettingRow(self.tr("Layer names"),
                                 self.tr("How the AI names the layers it creates."),
                                 self._naming_segments, group))
        return group

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
        self._show_saved()
        self._emit_profile()

    def _on_units_changed(self, value: str) -> None:
        value = normalize_units(value)
        if value == self._store.units:
            return
        self._store.units = value
        self._show_saved()
        self._emit_profile()

    def _on_naming_changed(self, value: str) -> None:
        value = normalize_layer_naming(value)
        if value == self._store.layer_naming:
            return
        self._store.layer_naming = value
        self._show_saved()
        self._emit_profile()

    def _pro_card(self, host, note: str) -> ProCard:
        """The card over a section this plan does not include."""










        card = ProCard(self.tr("Included with Pro"), note, self.tr("Unlock with Pro"), host)
        card.upgrade_requested.connect(self._on_upgrade)
        host.add(card)
        return card

    def _text_block(self, page: Page, title: str, placeholder: str, text: str, key: str,
                    pro_only: bool = False, pro_note: str = ""):
        page.add_group_title(title)


        card = SectionCard(page)
        page.add(card)



        pro = self._pro_card(card, pro_note) if pro_note else None
        if pro is not None:
            pro.setVisible(bool(pro_only))
        edit = QPlainTextEdit(card)
        edit.setStyleSheet(TEXTAREA_QSS)
        edit.setPlaceholderText(placeholder)
        edit.setPlainText(text or "")
        edit.setFixedHeight(scale_px_length(_TEXTAREA_H))
        edit.setTabChangesFocus(True)
        card.add(edit)
        count = QLabel(card)
        count.setStyleSheet(ROW_NOTE_QSS)
        count.setAlignment(Qt.AlignmentFlag.AlignRight)
        count.setContentsMargins(0, 0, 4, 0)
        card.add(count)
        self._update_count(edit, count)
        edit.textChanged.connect(lambda: self._on_profile_text_changed(edit, count, key))
        self._plan_sections.append((pro, (edit, count)))
        set_section_locked(edit, count, locked=bool(pro_only))
        return edit, count

    def _update_count(self, edit: QPlainTextEdit, count: QLabel) -> None:
        count.setText(self.tr("{count} / {limit}").format(
            count=len(edit.toPlainText()), limit=PROFILE_MAX_CHARS))

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



    def _build_memory(self, page: Page) -> None:
        """The notes, the field that adds one, and the switch that lets the AI add its own."""









        page.add_group_title(self.tr("Memory"))
        locked = self._memory_locked()
        card = SectionCard(page)
        page.add(card)
        pro = self._pro_card(
            card, self.tr("Pro reads your notes at the start of every conversation."))
        pro.setVisible(locked)
        intro = QLabel(self.tr("Short reminders it keeps between conversations."), card)
        intro.setStyleSheet(ROW_NOTE_QSS)
        intro.setWordWrap(True)
        intro.setContentsMargins(2, 0, 2, 0)
        card.add(intro)
        self._notes_group = SettingGroup(card, flat=True)
        card.add(self._notes_group)

        add_row = QWidget(card)
        add_lay = QHBoxLayout(add_row)
        add_lay.setContentsMargins(0, 0, 0, 0)
        add_lay.setSpacing(8)
        self._note_edit = QLineEdit(add_row)
        self._note_edit.setStyleSheet(INPUT_QSS)
        self._note_edit.setPlaceholderText(self.tr("I work in EPSG:2154"))
        self._note_edit.setMaxLength(MEMORY_NOTE_MAX_CHARS)
        self._note_edit.returnPressed.connect(self._on_add_note)
        add_lay.addWidget(self._note_edit, 1)
        add_btn = QPushButton(self.tr("Add"), add_row)
        add_btn.setStyleSheet(GHOST_BTN_QSS)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setAutoDefault(False)
        add_btn.clicked.connect(self._on_add_note)
        add_lay.addWidget(add_btn, 0)
        card.add(add_row)

        group = SettingGroup(card, flat=True)
        self._memory_switch = Switch(group, self._store.memory_enabled)
        self._memory_switch.toggled.connect(self._on_memory_toggled)
        group.add_row(SettingRow(self.tr("Let the AI add its own notes"), "",
                                 self._memory_switch, group))




        folder_btn = QPushButton(self.tr("Open"), group)
        folder_btn.setStyleSheet(GHOST_BTN_QSS)
        folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_btn.setAutoDefault(False)
        folder_btn.clicked.connect(self._on_open_memory_folder)
        folder_btn.setEnabled(bool(memory_dir()))
        group.add_row(SettingRow(self.tr("Memory folder"),
                                 self.tr("Your notes as Markdown files on this computer. "
                                         "Reword or delete one there and the next conversation follows."),
                                 folder_btn, group))
        card.add(group)
        self._fill_notes()


        self._plan_sections.append((pro, (self._notes_group, add_row, group, intro)))
        set_section_locked(self._notes_group, add_row, group, intro, locked=locked)

    def _fill_notes(self) -> None:
        self._notes_group.clear()
        notes = load_memory_notes(self._store)
        if not notes:
            empty = QLabel(self.tr("No notes yet."), self._notes_group)
            empty.setStyleSheet(ROW_NOTE_QSS)
            empty.setWordWrap(True)
            empty.setContentsMargins(14, 12, 14, 12)
            self._notes_group.add_row(empty)
            return
        for note in reversed(notes):
            row = NoteRow(note["text"], self._note_caption(note), self._notes_group, note["id"])
            row.removed.connect(self._on_remove_note)
            row.edited.connect(self._on_edit_note)
            self._notes_group.add_row(row)
        apply_font_scale_to_tree(self._notes_group)

    def _open_project_key(self) -> str:
        """The key of the project open right now, or "" outside QGIS."""
        try:
            from qgis.core import QgsProject
            return project_key(QgsProject.instance().fileName())
        except Exception:  # noqa: BLE001 - no project is not an error here
            return ""

    def _note_caption(self, note: dict) -> str:
        """Who noted it, when, and whether it belongs to this project."""










        who = self.tr("Noted by the AI") if note.get("source") == "ai" else self.tr("Added by you")
        parts = [who]
        if note.get("scope") == "project":
            key = str(note.get("project") or "")
            here = self._open_project_key()
            parts.append(self.tr("This project") if key and key == here
                         else self.tr("Another project"))
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
        self._note_edit.clear()
        self._fill_notes()
        self._show_saved()
        self._emit_profile()

    def _on_remove_note(self, text: str) -> None:
        if remove_memory_note(self._store, text):
            self._fill_notes()
            self._show_saved()
            self._emit_profile()

    def _on_edit_note(self, note_id: str, text: str) -> None:
        """The user rewords a note in place: same note, new sentence."""






        note = next((n for n in load_memory_notes(self._store) if n["id"] == note_id), None)
        if note is None:
            return
        if add_memory_note(self._store, text, "user", note["kind"], note["scope"],
                           note["project"], note_id) is None:
            return
        self._fill_notes()
        self._show_saved()
        self._emit_profile()

    def _on_open_memory_folder(self) -> None:
        """Show the notes as what they are: files on this computer."""





        save_memory_notes(self._store, load_memory_notes(self._store))
        path = memory_dir()
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_memory_toggled(self, on: bool) -> None:
        self._store.memory_enabled = bool(on)
        self._show_saved()
        self._emit_profile()

    def _sync_from_store(self) -> None:
        """Repaint every control from the store, without re-saving."""
        self._sync_language_combo()
        self._style_segments.set_value(self._store.reply_style)
        self._questions_segments.set_value(self._store.question_policy)
        self._expertise_segments.set_value(self._store.expertise)
        self._units_segments.set_value(self._store.units)
        self._naming_segments.set_value(self._store.layer_naming)
        self._sync_timeout_combo()
        self._values["send_shortcut"] = self._store.send_shortcut
        for edit, value in ((self._name_edit, self._store.profile_name),
                            (self._role_edit, self._store.profile_role)):
            edit.blockSignals(True)
            edit.setText(value)
            edit.blockSignals(False)
        for switch, value in ((self._explain_switch, self._store.explain_runs),
                              (self._tools_switch, self._store.show_tool_details),
                              (self._follow_switch, self._store.follow_edits),
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
