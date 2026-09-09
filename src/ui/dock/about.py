# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Update card, keyboard shortcuts and Contact us: the "about" surfaces the TerraLab plugins share, adapted to a chat panel."""






from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QKeySequence, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..external_links import open_external_url
from ..font_scale import apply_font_scale_to_tree, scale_px_length, scale_qss_font_px, widget_pixel_ratio
from ..shared import PLUGIN_ICON_PATH, PRODUCT_NAME, SUPPORT_EMAIL, exec_dialog, plugin_version, size_within_screen, tr
from ..style import (
    _BTN_GHOST,
    _BTN_PRIMARY,
    _BTN_PRIMARY_WIDE,
    BTN_PILL_PX,
    BTN_PRIMARY_WIDE_PX,
    BTN_PX,
    CHIP_PX,
    FIELD,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    INK,
    INK_2,
    INK_3,
    LINE,
    MONO_FAMILY,
    PAGE,
    RADIUS_CARD,
    RADIUS_CHIP,
    SURFACE,
)
from ..styles import (
    _UPDATE_CARD_STYLE,
    _UPDATE_LATER_STYLE,
    _UPDATE_NOTE_STYLE,
    _UPDATE_TITLE_STYLE,
    MUTED_TEXT,
)
from ..terralab_menu import open_plugin_manager_updates



_SHORTCUTS_QSS = scale_qss_font_px(
    f"QDialog#shortcutsDialog {{ background: {PAGE}; }}"
    f"QLabel#shTitle {{ font-size: 16px; font-weight: 600; color: {INK};"
    " background: transparent; }"
    f"QLabel#shSub {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; }}"
    f"QFrame#shCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#shColumnRule {{ background: {LINE}; border: none;"
    " min-width: 1px; max-width: 1px; }"
    f"QLabel#shSection {{ font-size: {FONT_MICRO}px; font-weight: 600;"
    f" letter-spacing: 0.7px; color: {INK_3}; background: transparent; }}"
    f"QLabel#shAction {{ font-size: {FONT_BASE}px; color: {INK}; background: transparent; }}"
    f"QLabel#shNote {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; }}"
    f"QLabel#shJoin {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; }}"
    f"QLabel#shKey {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px; color: {INK_2};"
    f" background: {FIELD}; border: 1px solid {LINE}; border-radius: {RADIUS_CHIP}px;"
    " padding: 0 7px; }"
    f"QLabel#shWord {{ font-size: {FONT_HINT}px; color: {INK_2}; background: {FIELD};"
    f" border: 1px solid {LINE}; border-radius: {RADIUS_CHIP}px; padding: 0 7px; }}"
)


class UpdateBanner(QWidget):
    """The update card, full width at the top of the dock, hidden until an installable version is known."""



    update_clicked = pyqtSignal(str)
    dismissed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.version = ""



        policy = self.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 0)
        outer.setSpacing(0)
        self._card = QWidget(self)
        self._card.setObjectName("updateCard")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._card.setStyleSheet(_UPDATE_CARD_STYLE)
        policy = self._card.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self._card.setSizePolicy(policy)
        card = QVBoxLayout(self._card)
        card.setContentsMargins(12, 12, 12, 12)
        card.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        icon_label = QLabel(self._card)
        icon_label.setFixedSize(36, 36)



        ratio = widget_pixel_ratio(icon_label)
        side = max(1, int(round(36 * ratio)))
        pixmap = QPixmap(PLUGIN_ICON_PATH)
        if not pixmap.isNull():
            pixmap = pixmap.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            icon_label.setPixmap(pixmap)
        head.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        self._title = QLabel("", self._card)
        self._title.setWordWrap(True)
        self._title.setStyleSheet(_UPDATE_TITLE_STYLE)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        words.addWidget(self._title)
        self._note = QLabel("", self._card)
        self._note.setWordWrap(True)





        policy = self._note.sizePolicy()
        policy.setHeightForWidth(True)
        self._note.setSizePolicy(policy)
        self._note.setStyleSheet(_UPDATE_NOTE_STYLE)


        self._note.setTextFormat(Qt.TextFormat.PlainText)
        words.addWidget(self._note)
        head.addLayout(words, 1)
        card.addLayout(head)

        self._update_btn = QPushButton(self.tr("Update now"), self._card)
        self._update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_btn.setStyleSheet(_BTN_PRIMARY_WIDE)
        self._update_btn.setFixedHeight(BTN_PRIMARY_WIDE_PX)
        self._update_btn.setAutoDefault(False)
        self._update_btn.clicked.connect(self._on_update)
        card.addWidget(self._update_btn)

        later_row = QHBoxLayout()
        later_row.setContentsMargins(0, 0, 0, 0)
        later_row.addStretch()
        self._later_btn = QPushButton(self.tr("Later"), self._card)
        self._later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later_btn.setStyleSheet(_UPDATE_LATER_STYLE)
        self._later_btn.setAutoDefault(False)
        self._later_btn.clicked.connect(self._on_later)
        later_row.addWidget(self._later_btn)
        later_row.addStretch()
        card.addLayout(later_row)
        outer.addWidget(self._card)
        self.hide()

    def offer(self, version: str, note: str = "") -> None:
        """Offer one version, with the served line about what it brings."""
        self.version = version or ""
        self._title.setText(self.tr("{product} {version} is out").format(
            product=PRODUCT_NAME, version=self.version))
        self._note.setText(note or "")
        self._note.setVisible(bool(note))
        self.setVisible(bool(self.version))

    def _on_update(self) -> None:
        self.update_clicked.emit(self.version)
        open_plugin_manager_updates()

    def _on_later(self) -> None:
        self.hide()
        self.dismissed.emit(self.version)


def _kbd(text: str, parent, mono: bool = True) -> QLabel:
    """One key cap. Mono for a key, the panel's own face for a control's name."""
    cap = QLabel(text, parent)
    cap.setObjectName("shKey" if mono else "shWord")
    cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
    cap.setFixedHeight(scale_px_length(CHIP_PX))
    return cap


def _shortcut_row(parent, action: str, note: str, caps: tuple) -> QWidget:
    """A row of the map: what it does on the left, the caps on the right."""




    row = QWidget(parent)
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(10)
    words = QVBoxLayout()
    words.setContentsMargins(0, 0, 0, 0)
    words.setSpacing(1)
    title = QLabel(action, row)
    title.setObjectName("shAction")
    title.setWordWrap(True)
    words.addWidget(title)
    if note:
        hint = QLabel(note, row)
        hint.setObjectName("shNote")
        hint.setWordWrap(True)
        words.addWidget(hint)
    line.addLayout(words, 1)
    keys = QHBoxLayout()
    keys.setContentsMargins(0, 0, 0, 0)
    keys.setSpacing(4)
    for index, (label, mono) in enumerate(caps):
        if index:
            joiner = QLabel(tr("or"), row)
            joiner.setObjectName("shJoin")
            keys.addWidget(joiner)
        keys.addWidget(_kbd(label, row, mono))
    line.addLayout(keys, 0)
    line.setAlignment(keys, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
    return row


def _shortcut_column(parent, sections: tuple) -> QWidget:
    """One column of the sheet: a micro label, then its rows."""
    column = QWidget(parent)
    col = QVBoxLayout(column)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(6)
    for index, (heading, rows) in enumerate(sections):
        label = QLabel(heading.upper(), column)
        label.setObjectName("shSection")
        label.setContentsMargins(0, 14 if index else 0, 0, 6)
        col.addWidget(label)
        for action, note, caps in rows:
            col.addWidget(_shortcut_row(column, action, note, caps))
    col.addStretch(1)
    return column


def shortcut_columns() -> tuple:
    """The whole keyboard map, as the two columns the card is built from."""







    def native(seq) -> str:
        return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)

    def key(label: str) -> tuple:
        return ((label, True),)

    def key_or(label: str, word: str) -> tuple:
        return ((label, True), (word, False))

    def word(label: str) -> tuple:
        return ((label, False),)

    enter_key = native("Return")
    esc_key = native("Esc")
    composer = (
        (tr("Send the message"), "", key(enter_key)),
        (tr("New line"), "", key(native("Shift+Return"))),
        (tr("Add a layer from the project"), tr("Or drag it from the Layers panel"), key("@")),
        (tr("Run a panel command"), tr("New chat, history, settings"), key("/")),
        (tr("Open the command list"), "", key(native("Ctrl+/"))),
        (tr("Bring back the last message you sent"), "", key(native("Up"))),
        (tr("Close the list, or stop the run"), "", key(esc_key)),
    )
    panel = (
        (tr("Open or close AI Agent"), "", key(native("Ctrl+Alt+A"))),
    )
    history = (
        (tr("Go back one step in the agent's work"), tr("In this chat"),
         key(native("Ctrl+Alt+Z"))),
        (tr("Go forward one step"), "", key(native("Ctrl+Alt+Shift+Z"))),


        (tr("Go back to an earlier state of your project"), "", key(native("Ctrl+Alt+H"))),
    )
    during = (
        (tr("Stop the run"), "", key_or(esc_key, tr("Stop button"))),
        (tr("Answer a permission card"), "", word(tr("Allow / Skip"))),
    )
    chats = (
        (tr("Open a recent chat"), "", word(tr("History button"))),
        (tr("Start a new chat"), "", word(tr("Pencil button"))),
    )


    return (
        ((tr("Composer"), composer), (tr("Panel"), panel)),
        ((tr("History"), history), (tr("During a run"), during), (tr("Chats"), chats)),
    )


def build_shortcuts_card(parent) -> QFrame:
    """The two-column card of the whole map, styled on its own."""




    left_sections, right_sections = shortcut_columns()
    card = QFrame(parent)
    card.setObjectName("shCard")
    card.setStyleSheet(_SHORTCUTS_QSS)
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    columns = QHBoxLayout(card)
    columns.setContentsMargins(16, 14, 16, 14)
    columns.setSpacing(28)
    columns.addWidget(_shortcut_column(card, left_sections), 1)
    rule = QFrame(card)
    rule.setObjectName("shColumnRule")
    rule.setFrameShape(QFrame.Shape.NoFrame)
    columns.addWidget(rule)
    columns.addWidget(_shortcut_column(card, right_sections), 1)
    return card


def show_shortcuts_dialog(parent=None) -> None:
    """The chat panel's keyboard map, grouped by where each key is live."""






    dlg = QDialog(parent)
    dlg.setObjectName("shortcutsDialog")
    dlg.setWindowTitle(tr("Keyboard shortcuts"))
    dlg.setStyleSheet(_SHORTCUTS_QSS)




    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(14)
    title = QLabel(tr("Keyboard shortcuts"), dlg)
    title.setObjectName("shTitle")
    layout.addWidget(title)
    subtitle = QLabel(tr("Everything the panel does without leaving the keyboard."), dlg)
    subtitle.setObjectName("shSub")
    subtitle.setWordWrap(True)
    layout.addWidget(subtitle)

    card = build_shortcuts_card(dlg)
    layout.addWidget(card)

    foot = QHBoxLayout()
    foot.setContentsMargins(0, 0, 0, 0)
    foot.addStretch(1)
    ok_btn = QPushButton(tr("Done"), dlg)
    ok_btn.setStyleSheet(_BTN_PRIMARY)
    ok_btn.setFixedHeight(BTN_PX)
    ok_btn.setMinimumWidth(scale_px_length(96))
    ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ok_btn.setAutoDefault(False)
    ok_btn.clicked.connect(dlg.accept)
    foot.addWidget(ok_btn)
    layout.addLayout(foot)

    apply_font_scale_to_tree(dlg)
    dlg.adjustSize()
    size_within_screen(dlg, max(dlg.width(), scale_px_length(660)), dlg.height(),
                       scale_px_length(480), 0)
    exec_dialog(dlg)


def show_contact_dialog(parent=None, support_email: str = SUPPORT_EMAIL,
                        call_url: str = "") -> None:
    """Bug, question, feature request: the address, and a call when served."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("Contact us"))
    dlg.setMinimumWidth(scale_px_length(350))
    dlg.setMaximumWidth(scale_px_length(450))
    lay = QVBoxLayout(dlg)
    lay.setSpacing(10)
    lay.setContentsMargins(16, 16, 16, 16)

    msg = QLabel(tr("Bug, question, feature request?") + "\n" + tr("We read every message."), dlg)
    msg.setWordWrap(True)
    msg.setStyleSheet("font-size: 12px; color: palette(text);")
    lay.addWidget(msg)

    email_label = QLabel(f"<b>{support_email}</b>", dlg)
    email_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    email_label.setStyleSheet("font-size: 12px; color: palette(text);")
    lay.addWidget(email_label)

    version = plugin_version()
    if version:
        version_label = QLabel(tr("{product} {version}").format(
            product=PRODUCT_NAME, version=version), dlg)
        version_label.setStyleSheet(f"font-size: 11px; color: {MUTED_TEXT};")
        lay.addWidget(version_label)

    copy_btn = QPushButton(tr("Copy email address"), dlg)
    copy_btn.setStyleSheet(_BTN_PRIMARY)
    copy_btn.setFixedHeight(BTN_PILL_PX)
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _copy() -> None:
        QApplication.clipboard().setText(support_email)
        copy_btn.setText(tr("Copied"))

    copy_btn.clicked.connect(_copy)
    lay.addWidget(copy_btn)

    if call_url:
        or_label = QLabel(tr("or"), dlg)
        or_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        or_label.setStyleSheet("color: palette(text); font-size: 11px;")
        lay.addWidget(or_label)
        call_btn = QPushButton(tr("Book a video call"), dlg)
        call_btn.setStyleSheet(_BTN_GHOST)
        call_btn.setFixedHeight(BTN_PILL_PX)
        call_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        call_btn.clicked.connect(lambda: open_external_url(call_url, parent=dlg))
        lay.addWidget(call_btn)

    apply_font_scale_to_tree(dlg)
    exec_dialog(dlg)
