# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer at the bottom of the panel: the site's Prompt Bar, Rounded."""






















from __future__ import annotations

import os
import sys

from qgis.PyQt.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.layer_mime import ChipRow, layer_chip, layer_ids_from_mime, mime_has_layers
from ..core.prompt_quality import NOT_A_TASK, TOO_SHORT, check_prompt
from .attach_menu import AttachPopover
from .attachments import (
    ATTACHMENT_BUDGET_BYTES,
    MAX_ATTACHMENTS,
    AttachmentTag,
    any_filter,
    attachment_from_path,
    classify,
    image_attachment,
    wire_shape,
)
from .composer_input import ComposerInput
from .effort_chip import EffortChip, effort_texts
from .icons import icon_for, paper_of
from .layer_card import LayerCard
from .layer_icons import resolve_layer
from .library import ExamplesDialog
from .permission_chip import PermissionChip
from .shared import exec_dialog
from .style import (
    _BTN_SEND,
    ACCENT_BORDER,
    ACCENT_TINT,
    ACCENT_TINT_ON,
    BTN_PX,
    FONT_HINT,
    INK,
    INK_2,
    INK_3,
    LINE,
    ON_ACCENT,
    PAGE,
    SPACE_CARD,
    SPACE_OUTER,
    SPACE_TIGHT,
    qcolor,
    repolish,
)
from .styles import WARNING_TEXT
from .widgets import FlowLayout, IconButton

_SEND_SIZE = BTN_PX
_DISC_GLYPH = 16
_HINT_MS = 2500
_HINT_QSS = f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none;"

_HINT_WARN_QSS = f"font-size: {FONT_HINT}px; color: {WARNING_TEXT}; background: transparent; border: none;"
_HINT_WARN_MS = 6000
_ROW_PAD_X = SPACE_OUTER
_CHIP_GAP = SPACE_TIGHT


_ROW_MIN_GAP = 12


_ROW_HYSTERESIS = 16
_ATTACH_PX = 28
_ATTACH_GLYPH = 16




_ATTACH_BTN_QSS = (
    f"QToolButton#attachButton {{ background: transparent; border: 1px solid {LINE};"
    f" padding: 0; border-radius: {_ATTACH_PX // 2}px; }}"
    f"QToolButton#attachButton:hover {{ border-color: {ACCENT_BORDER};"
    f" background: {ACCENT_TINT}; }}"
    'QToolButton#attachButton:pressed, QToolButton#attachButton[active="true"] {'
    f" border-color: {ACCENT_BORDER}; background: {ACCENT_TINT_ON}; }}"
    f"QToolButton#attachButton:disabled {{ border-color: {LINE}; }}"
    "QToolButton#attachButton::menu-indicator { image: none; width: 0; }"
)


def _same_attachment(left, right) -> bool:
    """Whether two attachment paths name one file, in Windows spelling too."""
    if not left or not right:
        return left == right
    try:
        return (os.path.normcase(os.path.abspath(str(left)))
                == os.path.normcase(os.path.abspath(str(right))))
    except (OSError, ValueError):
        return str(left) == str(right)


class _AttachButton(IconButton):
    """The ``+``: the third ink inside the ring, the full ink under the pointer."""

    def __init__(self, parent, tooltip: str):
        super().__init__(parent, tooltip=tooltip, qss=_ATTACH_BTN_QSS)
        self.setObjectName("attachButton")
        self.setStyleSheet(_ATTACH_BTN_QSS)
        self.setFixedSize(_ATTACH_PX, _ATTACH_PX)
        self.paint_glyph(False)

    def paint_glyph(self, hovered: bool) -> None:
        self.set_icon("plus", _ATTACH_GLYPH, qcolor(INK if hovered else INK_3))

    def enterEvent(self, event):  # noqa: N802 - Qt override
        super().enterEvent(event)
        if self.isEnabled():
            self.paint_glyph(True)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.paint_glyph(False)


class Composer(QFrame):
    """Text, attachments, context, and the Send/Stop button."""

    send_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    files_attached = pyqtSignal(object)
    files_dropped = pyqtSignal(object)
    context_add_requested = pyqtSignal(str)
    attachments_changed = pyqtSignal()
    chips_changed = pyqtSignal()
    chip_removed = pyqtSignal(str, str)
    layer_card_clicked = pyqtSignal(str)
    permission_mode_changed = pyqtSignal(str)
    effort_changed = pyqtSignal(str)
    upgrade_requested = pyqtSignal()
    example_chosen = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("composer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.setAcceptDrops(False)
        self.setProperty("focused", False)
        self._running = False
        self._blocked = False


        self._empty_chat = True
        self._blocked_placeholder = ""


        self._effort_locked = False


        self._compact_controls = False
        self._last_sent = ""
        self._offline = False
        self._items: list[dict] = []
        self._tags: dict[str, AttachmentTag] = {}
        self._chips = ChipRow()
        self._cards: dict[tuple, LayerCard] = {}
        self._attach: AttachPopover | None = None
        self._placeholder = self.tr("Give the AI agent a task in QGIS...")

        col = QVBoxLayout(self)
        col.setContentsMargins(_ROW_PAD_X + 2, SPACE_OUTER, _ROW_PAD_X, _ROW_PAD_X)
        col.setSpacing(SPACE_CARD)


        self._attach_host = QWidget(self)
        self._attach_flow = FlowLayout(self._attach_host, SPACE_CARD, SPACE_TIGHT)
        self._attach_host.hide()
        col.addWidget(self._attach_host)



        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_OUTER)

        self._attach_btn = _AttachButton(
            self,
            self.tr("Add photos, files or one of this project's layers. A layer can also be"
                    " dragged from the Layers panel; Ctrl+V pastes a picture."))



        self._attach_btn.clicked.connect(self.open_attach)
        row.addWidget(self._attach_btn, 0, Qt.AlignmentFlag.AlignVCenter)




        row.addSpacing(_CHIP_GAP - SPACE_OUTER)
        self._permission_chip = PermissionChip(self)
        self._permission_chip.mode_changed.connect(self.permission_mode_changed.emit)
        self._permission_chip.upgrade_requested.connect(self.upgrade_requested.emit)
        row.addWidget(self._permission_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)

        self._input = ComposerInput(self)
        self._input.setPlaceholderText(self._placeholder)
        self._input.set_sheet_anchor(self)
        self._input.submitted.connect(self._on_submit)
        self._input.textChanged.connect(self._sync_send_enabled)
        self._input.textChanged.connect(self._clear_warning)
        self._input.mentions_changed.connect(self._on_mentions_changed)
        self._input.files_requested.connect(self._on_add_files)
        self._input.recall_requested.connect(self._recall_last)
        self._input.image_pasted.connect(lambda image: self.add_image(image, "pasted.png"))
        self._input.paths_pasted.connect(self.add_paths)
        self._input.installEventFilter(self)
        col.addWidget(self._input)

        self._effort_chip = EffortChip(self)
        self._effort_chip.effort_changed.connect(self._on_effort_pick)
        self._effort_chip.upgrade_requested.connect(self.upgrade_requested.emit)
        row.addWidget(self._effort_chip, 0, Qt.AlignmentFlag.AlignVCenter)

        self._send_btn = QToolButton(self)
        self._send_btn.setObjectName("sendBtn")
        self._send_btn.setStyleSheet(_BTN_SEND)
        self._send_btn.setFixedSize(_SEND_SIZE, _SEND_SIZE)
        self._send_btn.setIconSize(QSize(_DISC_GLYPH, _DISC_GLYPH))
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._send_btn.setProperty("running", False)
        self._send_btn.clicked.connect(self._on_send_or_stop)
        row.addWidget(self._send_btn, 0, Qt.AlignmentFlag.AlignVCenter)



        self._hint = QLabel(self)
        self._hint.setStyleSheet(_HINT_QSS)
        self._hint.setWordWrap(True)
        self._hint.setContentsMargins(2, 0, 0, 0)
        self._hint.hide()
        col.addWidget(self._hint)
        col.addLayout(row)
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self._hint.hide)

        self._paint_send()
        self._sync_send_enabled()
        self._sync_controls()



    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_controls()

    def _chip_width(self, chip, compact: bool) -> int:
        """What ``chip`` would take with or without its word."""





        measure = getattr(chip, "width_for", None)
        if callable(measure):
            try:
                return int(measure(compact))
            except (TypeError, RuntimeError):
                pass
        return chip.sizeHint().width()

    def _sync_controls(self) -> None:
        """Keep every word on the control row while it fits; drop the words rather than let Qt cut into them."""













        margins = self.contentsMargins()
        room = self.width() - margins.left() - margins.right()
        if room <= 0:
            return






        fixed = (_ATTACH_PX
                 + _CHIP_GAP
                 + _SEND_SIZE
                 + 2 * SPACE_OUTER
                 + _ROW_MIN_GAP)
        full = (fixed
                + self._chip_width(self._permission_chip, False)
                + self._chip_width(self._effort_chip, False))
        compact = self._compact_controls
        if not compact and full > room:
            compact = True
        elif compact and full + _ROW_HYSTERESIS <= room:
            compact = False
        if compact == self._compact_controls:
            return
        self._compact_controls = compact
        for chip in (self._permission_chip, self._effort_chip):
            setter = getattr(chip, "set_compact", None)
            if callable(setter):
                setter(compact)

    def controls_are_compact(self) -> bool:
        """Whether the row is showing the short chips. For the panel's checks."""
        return self._compact_controls



    def text(self) -> str:
        return self._input.text()

    def set_text(self, text: str) -> None:
        self._input.set_text(text)

    def clear(self) -> None:
        self._input.clear()

    def focus_input(self) -> None:
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_send_shortcut(self, value: str) -> None:
        """"enter" or "modifier", from Settings > General."""
        self._input.set_send_on_modifier(str(value) == "modifier")
        self._paint_send()

    def _send_help(self) -> str:
        """The send button's help, built from the gesture actually accepted."""






        modifier = self.tr("Cmd") if sys.platform == "darwin" else self.tr("Ctrl")
        if self._input.sends_on_modifier():
            return self.tr("Send ({mod}+Enter). Enter for a new line.").format(mod=modifier)
        return self.tr("Send (Enter). Shift+Enter for a new line.")

    def set_empty_chat(self, empty: bool) -> None:
        """A fresh chat gets the taller box; a running one gets it back."""




        from .composer_input import EMPTY_CHAT_LINES, MIN_LINES

        self._empty_chat = bool(empty)
        self._input.set_min_lines(EMPTY_CHAT_LINES if empty else MIN_LINES)

    def set_mentions(self, items) -> None:
        self._input.set_mentions(items)

    def set_mention_provider(self, provider) -> None:
        """``provider() -> [{kind, label, value}]``, asked every time the user types ``@``."""
        self._input.set_mention_provider(provider)

    def set_permission_mode(self, approval: str) -> None:
        """Show the settings' approval on the chip without emitting."""
        self._permission_chip.set_mode(approval)

    def permission_mode(self) -> str:
        return self._permission_chip.mode()

    def set_effort(self, effort: str) -> None:
        """Show the settings' effort level on the chip without emitting."""
        self._effort_chip.set_effort(effort)
        self._sync_effort_lock()

    def effort(self) -> str:
        return self._effort_chip.effort()

    def _on_effort_pick(self, effort: str) -> None:
        self._sync_effort_lock()
        self.effort_changed.emit(effort)

    def set_paid_plan(self, paid: bool) -> None:
        """The session frame's plan: the paid efforts and Autopilot are pickable, or they carry Upgrade."""
        self._effort_chip.set_paid(paid)
        self._permission_chip.set_paid(paid)
        self._sync_effort_lock()

    def _sync_effort_lock(self) -> None:
        """A paid level under the handle on a free account locks the box."""




        locked = self._effort_chip.is_locked()
        flipped = locked != self._effort_locked
        self._effort_locked = locked



        if locked or flipped:
            self._apply_placeholder()
        if not flipped:
            return
        self._input.setReadOnly(self._blocked or locked)
        self._attach_btn.setEnabled(not (self._blocked or locked))
        self._paint_send()
        self._sync_send_enabled()

    def is_effort_locked(self) -> bool:
        return self._effort_locked

    def _apply_placeholder(self) -> None:
        if self._blocked and self._blocked_placeholder:
            self._input.setPlaceholderText(self._blocked_placeholder)
        elif self._effort_locked and not self._blocked:
            names = {e: n for e, n, _note in effort_texts(self._effort_chip)}
            level = names.get(self._effort_chip.chosen(), "")
            line = self.tr("{level} effort needs Pro. Pick Low, or upgrade.")
            self._input.setPlaceholderText(line.format(level=level))
        else:
            self._input.setPlaceholderText(self._placeholder)

    def set_placeholder(self, text: str) -> None:
        """The prompt in the empty box; a blocked or locked box keeps its own line."""
        self._placeholder = text or ""
        self._apply_placeholder()

    def show_hint(self, text: str) -> None:
        """One muted line under the text, gone after a moment."""
        self._hint.setStyleSheet(_HINT_QSS)
        self._hint.setText(text)
        self._hint.show()
        self._hint_timer.start(_HINT_MS)

    def show_warning(self, text: str) -> None:
        """The same line in amber, for a message the box would not send."""

        if not text:
            return
        self._hint.setStyleSheet(_HINT_WARN_QSS)
        self._hint.setText(text)
        self._hint.show()
        self._hint_timer.start(_HINT_WARN_MS)
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def hint_text(self) -> str:
        return self._hint.text() if self._hint.isVisible() else ""

    def _clear_warning(self) -> None:
        if self._hint.isVisible():
            self._hint_timer.stop()
            self._hint.hide()



    def examples_dialog(self) -> ExamplesDialog:
        """The library window, built fresh each time it is opened."""







        dialog = ExamplesDialog(self.window() or self)
        dialog.prompt_chosen.connect(self._on_example_prompt)
        dialog.example_chosen.connect(self.example_chosen.emit)
        return dialog

    def open_examples(self) -> None:
        """Open the Examples library."""








        if self._blocked:
            return
        dialog = self.examples_dialog()
        try:
            exec_dialog(dialog)
        finally:




            dialog.deleteLater()

    def _on_example_prompt(self, text: str) -> None:
        """A chosen example lands in the box, ready to edit or send."""







        draft = self.text().strip()
        if not draft:
            self.set_text(text)
        else:
            self._input.append_text("\n\n" + text, at_end=True)
        self.focus_input()



    def attach_popover(self) -> AttachPopover:
        """The ``+`` sheet, built on first use and kept."""
        if self._attach is None:
            self._attach = AttachPopover(self)
            self._attach.add_files_requested.connect(self._on_add_files)
            self._attach.add_layer_requested.connect(self._input.begin_layer_mention)
            self._attach.installEventFilter(self)
        return self._attach

    def open_attach(self) -> None:
        """Open the ``+`` sheet above its button."""
        if self._blocked:
            return
        self._attach_btn.set_active(True)
        self.attach_popover().show_above(self._attach_btn, self.window())



    def attachments(self) -> list:
        """The attachments as ``send_requested`` carries them."""
        return [wire_shape(item) for item in self._items]

    def has_attachments(self) -> bool:
        return bool(self._items)

    def attachments_over_budget(self) -> dict | None:
        """The heaviest picture when they no longer fit in one message."""









        sized = [(len(str(item.get("data_base64") or "")), item)
                 for item in (wire_shape(x) for x in self._items)]
        if sum(size for size, _ in sized) <= ATTACHMENT_BUDGET_BYTES:
            return None
        return max(sized, key=lambda pair: pair[0])[1]

    def room_left(self) -> int:
        """How many more attachments this composer takes."""
        return max(0, MAX_ATTACHMENTS - len(self._items))

    def add_attachment(self, item: dict) -> bool:
        if (not isinstance(item, dict) or item.get("kind") not in ("image", "file")
                or len(self._items) >= MAX_ATTACHMENTS):
            if len(self._items) >= MAX_ATTACHMENTS:
                self.show_hint(self.tr("You can attach up to {n} items.").format(n=MAX_ATTACHMENTS))
            return False
        path = item.get("path")



        if path and any(_same_attachment(i.get("path"), path) for i in self._items):
            return False
        tag = AttachmentTag(item, self._attach_host)




        if tag.key in self._tags:
            tag.deleteLater()
            return False
        self._items.append(item)
        tag.removed.connect(self.remove_attachment)
        self._tags[tag.key] = tag
        self._attach_flow.addWidget(tag)
        self._attach_host.show()
        self._attach_host.updateGeometry()
        self.attachments_changed.emit()
        self._sync_send_enabled()
        return True

    def add_image(self, image: QImage, name: str = "image.png") -> bool:
        if image is None or image.isNull():
            self.show_hint(self.tr("No image in the clipboard."))
            return False
        item = image_attachment(image, name)
        return self.add_attachment(item) if item else False

    def add_paths(self, paths) -> list:
        """Attach every supported file; say which ones were not, and why."""







        accepted = []
        unsupported = []
        unreadable = []
        room = self.room_left()
        over = 0
        for path in list(paths or []):
            path = str(path)
            if not path or len(path) > 4096:
                continue
            if classify(path) is None:
                unsupported.append(path.replace("\\", "/").split("/")[-1])
                continue
            if len(accepted) >= room:
                over += 1
                continue
            item = attachment_from_path(path)
            if item is None:


                unreadable.append(path.replace("\\", "/").split("/")[-1])
                continue
            if self.add_attachment(item):
                accepted.append(path)
        self._report_rejections(unsupported, unreadable, over)
        return accepted

    def _report_rejections(self, unsupported, unreadable, over: int) -> None:
        """One line per cause, so the user fixes the right thing."""
        parts = []
        if unsupported:
            parts.append(self.tr("not a supported file: {names}")
                         .format(names=", ".join(unsupported[:3])))
        if unreadable:
            parts.append(self.tr("could not be read: {names}")
                         .format(names=", ".join(unreadable[:3])))
        if over:
            parts.append(self.tr("{n} left out, {total} at most")
                         .format(n=over, total=MAX_ATTACHMENTS))
        if parts:
            self.show_hint("; ".join(parts))

    def remove_attachment(self, key: str) -> None:
        tag = self._tags.pop(str(key), None)
        if tag is not None:
            self._attach_flow.removeWidget(tag)
            tag.deleteLater()
        self._items = [i for i in self._items
                       if str(i.get("id") or i.get("path") or i.get("name")) != str(key)]
        self._sync_row()
        self.attachments_changed.emit()
        self._sync_send_enabled()

    def clear_attachments(self) -> None:
        for key in list(self._tags):
            self.remove_attachment(key)
        self._items = []

    def _sync_row(self) -> None:
        self._attach_host.setVisible(bool(self._items) or bool(len(self._chips)))
        self._attach_host.updateGeometry()



    def chips(self) -> list:
        """The chips as ``send_requested`` carries them: ``{kind, label, value}``."""





        out = self._chips.chips()
        seen = {ChipRow.key(chip) for chip in out}
        for chip in self._input.mentions():
            if ChipRow.key(chip) not in seen:
                out.append(chip)
        return out

    def append_text(self, text: str) -> None:
        """Add words after what the box already holds."""
        self._input.append_text(text)

    def pin_mention(self, chip: dict) -> bool:
        """Write a chip into the text at the caret: what ``@`` inserts, from code."""




        return self._input.insert_chip(chip)

    def _on_mentions_changed(self) -> None:
        self.chips_changed.emit()
        self._sync_send_enabled()

    def add_chip(self, chip: dict) -> bool:
        """One card per layer; a chip already in the row is left alone."""
        if not self._chips.add(chip):
            return False
        card = LayerCard(chip, self._attach_host)
        card.chip_removed.connect(self._on_card_removed)
        card.layer_clicked.connect(self.layer_card_clicked.emit)
        self._cards[ChipRow.key(chip)] = card
        self._attach_flow.addWidget(card)
        self._attach_host.show()
        self._attach_host.updateGeometry()
        self.chips_changed.emit()
        return True

    def add_layers(self, layer_ids) -> list:
        """A card per project layer in ``layer_ids``; ids gone from the project are skipped."""
        added = []
        for layer_id in layer_ids or []:
            chip = layer_chip(resolve_layer(str(layer_id)))
            if chip and self.add_chip(chip):
                added.append(chip["value"])
        return added

    def remove_chip(self, kind: str, value: str) -> None:
        card = self._cards.pop((str(kind), str(value)), None)
        if card is not None:
            self._attach_flow.removeWidget(card)
            card.deleteLater()
        self._chips.remove(kind, value)
        self._sync_row()
        self.chips_changed.emit()

    def _on_card_removed(self, kind: str, value: str) -> None:
        self.remove_chip(kind, value)
        self.chip_removed.emit(kind, value)

    def clear_chips(self) -> None:
        for kind, value in list(self._cards):
            self.remove_chip(kind, value)
        self._chips.clear()

    def take_chips(self) -> list:
        """The chips of the message being sent; the row empties."""




        chips = self.chips()
        self.clear_chips()
        return chips

    def _on_add_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, self.tr("Add files or photos"), "", any_filter())
        self._attach_picked(paths)

    def _attach_picked(self, paths) -> None:
        accepted = self.add_paths(list(paths or []))
        if accepted:
            self.files_attached.emit(accepted)
            self.focus_input()



    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._send_btn.setProperty("running", self._running)
        repolish(self._send_btn)
        self._paint_send()
        self._sync_send_enabled()

    def is_running(self) -> bool:
        return self._running

    def set_blocked(self, blocked: bool, placeholder: str = "") -> None:
        """Read-only composer while the account cannot run anything."""
        self._blocked = bool(blocked)
        self._blocked_placeholder = placeholder or ""
        self._input.setReadOnly(self._blocked or self._effort_locked)
        self._apply_placeholder()
        self._attach_btn.setEnabled(not (self._blocked or self._effort_locked))
        self._permission_chip.setEnabled(not self._blocked)
        self._effort_chip.setEnabled(not self._blocked)
        self._paint_send()
        self._sync_send_enabled()

    def is_blocked(self) -> bool:
        return self._blocked

    def set_offline(self, offline: bool) -> None:
        """No server: the user can still type, Send waits for the line."""
        self._offline = bool(offline)
        self._sync_send_enabled()
        self._paint_send()

    def _paint_send(self) -> None:




        paper = paper_of(self._send_btn)
        disabled = paper
        if self._running:





            self._send_btn.setIcon(icon_for(self._send_btn, "stop", 16, qcolor(PAGE)))
            self._send_btn.setToolTip(self.tr("Stop the run"))
            self._send_btn.setAccessibleName(self.tr("Stop"))
        elif self._effort_locked and not self._blocked:


            self._send_btn.setIcon(icon_for(self._send_btn, "lock", _DISC_GLYPH, QColor(ON_ACCENT), disabled))
            self._send_btn.setToolTip(self.tr("This effort comes with Pro. See the plans."))
            self._send_btn.setAccessibleName(self.tr("Upgrade to Pro"))
        else:
            self._send_btn.setIcon(
                icon_for(self._send_btn, "arrow_up", _DISC_GLYPH, QColor(ON_ACCENT), disabled))
            self._send_btn.setToolTip(self.tr("Offline. Reconnecting...") if self._offline
                                      else self._send_help())
            self._send_btn.setAccessibleName(self.tr("Send"))

    def _sync_send_enabled(self) -> None:
        if self._running:
            self._send_btn.setEnabled(True)
            return
        if self._blocked or self._offline:
            self._send_btn.setEnabled(False)
            return
        if self._effort_locked:
            self._send_btn.setEnabled(True)
            return
        self._send_btn.setEnabled(bool(self.text().strip()) or bool(self._items))

    def _on_submit(self) -> None:
        if self._running or self._blocked or self._offline:
            return
        if self._effort_locked:
            self.upgrade_requested.emit()
            return
        if not (self.text().strip() or self._items):
            return




        reason = check_prompt(self.text(), len(self._items), len(self.chips()), first_message=self._empty_chat)
        if reason:
            self.show_warning(self._refusal(reason))
            return


        self._last_sent = self.text()
        self.send_clicked.emit()

    def _recall_last(self) -> None:



        if self._input.isReadOnly() or self._blocked or not self._last_sent:
            return
        self.set_text(self._last_sent)

    def _refusal(self, reason: str) -> str:
        if reason == NOT_A_TASK:
            return self.tr("That does not read like a task yet. Say what you want in a sentence.")
        if reason == TOO_SHORT:
            return self.tr("A few more words, please: what to do, and on which layer.")
        return ""

    def _on_send_or_stop(self) -> None:
        if self._running:
            self.stop_clicked.emit()
        else:
            self._on_submit()



    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched is self._input:
            if event.type() == QEvent.Type.FocusIn:
                self._set_flag("focused", True)
            elif event.type() == QEvent.Type.FocusOut:
                self._set_flag("focused", False)
        elif watched is self._attach and event.type() == QEvent.Type.Hide:
            self._attach_btn.set_hovered(False)
            self._attach_btn.set_active(False)
            self._attach_btn.paint_glyph(self._attach_btn.underMouse())
        return super().eventFilter(watched, event)

    def _set_flag(self, name: str, value: bool) -> None:
        if bool(self.property(name)) == value:
            return
        self.setProperty(name, value)
        repolish(self)



    @staticmethod
    def local_paths(mime) -> list:
        if mime is None or not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile()]

    @classmethod
    def accepts_mime(cls, mime) -> bool:
        """A layer from the Layers panel, local files, or a picture."""
        if mime is None:
            return False
        return bool(mime_has_layers(mime) or cls.local_paths(mime) or mime.hasImage())

    def take_drop(self, mime) -> bool:
        """The one drop path: layers become cards, files and pictures attachments."""
        layer_ids = layer_ids_from_mime(mime)
        if layer_ids:
            added = self.add_layers(layer_ids)
            if added:
                self.focus_input()
            return bool(added)
        paths = self.local_paths(mime)
        if paths:
            accepted = self.add_paths(paths)
            if accepted:
                self.files_dropped.emit(list(accepted))
            return bool(accepted)
        if mime is not None and mime.hasImage():
            return self.add_image(QImage(mime.imageData()), "dropped.png")
        return False
