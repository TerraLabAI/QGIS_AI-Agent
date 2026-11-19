# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

import html
import os
import sys
import time

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsGeometry,
    QgsNetworkAccessManager,
    QgsProject,
)
from qgis.PyQt.QtCore import QEvent, QLocale, QMimeData, QSize, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage, QTextCharFormat, QTextCursor
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import drafts, limits, telemetry, trace
from ..core import telemetry_events as ev
from ..core.layer_mime import ChipRow, layer_chip, layer_ids_from_mime, live_layer_chips, mime_has_layers
from ..core.prompt_quality import NOT_A_TASK, TOO_SHORT, check_prompt
from ..core.qt_compat import one_shot
from ..core.settings import account_tag
from .attach_card import TILE_GLYPH, AttachCard
from .attach_menu import AttachPopover
from .attachments import (
    MAX_ATTACHMENTS,
    MAX_DOCUMENT_FILE_BYTES,
    AttachmentTag,
    any_filter,
    attachment_from_path,
    classify,
    pasted_image_attachment,
    wire_shape,
)
from .composer_input import EMPTY_CHAT_LINES, MIN_LINES, ComposerInput
from .composer_suggest import ComposerSuggest
from .dictation_bar import ComposerDictation
from .effort_chip import EffortChip, effort_texts
from .file_preview import FilePreview
from .folder_card import FolderChip
from .font_scale import scale_qss_font_px
from .geo_paste import capabilities_url, count_layers, place, read_paste, splice
from .icons import icon_for, paper_of, pixmap_for
from .layer_card import LayerCard
from .layer_icons import layer_name, resolve_layer
from .library.dialog import HOME_KEY, SOURCES_KEY, ExamplesDialog
from .mention_search import live_mentions
from .permission_chip import PermissionChip
from .prompt_notice import PromptNotice
from .selection_line import SelectionLine
from .shared import exec_dialog, settle_hover
from .style import (
    _BTN_GHOST,
    _BTN_SEND,
    ACCENT_TINT,
    ACCENT_TINT_ON,
    BTN_PX,
    FILE_BADGES,
    FONT_BODY,
    FONT_HINT,
    HIT_PX,
    INK,
    INK_2,
    INK_3,
    INSET,
    LINE,
    ON_ACCENT,
    RADIUS_CONTROL,
    RED,
    SPACE_CARD,
    SPACE_OUTER,
    SPACE_TIGHT,
    TAGS,
    WEIGHT_MEDIUM,
    keyboard_ring,
    qcolor,
    repolish,
)
from .styles import WARNING_TEXT
from .widgets import ElidedLabel, FlowLayout, IconButton

_SEND_SIZE = BTN_PX
_DISC_GLYPH = 16
_HINT_MS = 2500
STOP_AFTER_SEND_S = 0.6
_OFFLINE_GRACE_MS = 15000
_HINT_QSS = scale_qss_font_px(f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none;")
_STALE_NAME_QSS = scale_qss_font_px(f"font-size: {FONT_BODY}px; font-weight: {WEIGHT_MEDIUM}; color: {RED};"
                                    " background: transparent; border: none;")
_STALE_BUTTON_QSS = scale_qss_font_px(_BTN_GHOST.replace(f"font-size: {FONT_BODY}px", f"font-size: {FONT_HINT}px"))
_STALE_GLYPH = 13
_STALE_NAME_PX = 150
_STALE_BUTTON_PX = 22

_STALE_ROWS = 3

_HINT_WARN_QSS = scale_qss_font_px(f"font-size: {FONT_HINT}px; color: {WARNING_TEXT};"
                                   " background: transparent; border: none;")
_HINT_WARN_MS = 6000
_ROW_PAD_X = SPACE_OUTER
_CHIP_GAP = SPACE_TIGHT


_ROW_MIN_GAP = 12


EMPTY_CHAT_GAP = 8


_ROW_HYSTERESIS = 16
_ATTACH_PX = 28
_ATTACH_GLYPH = 18




_ATTACH_BTN_QSS = (
    "QToolButton#attachButton { background: transparent; border: none;"
    f" padding: 0; border-radius: {_ATTACH_PX // 2}px; }}"
    f"QToolButton#attachButton:hover {{ background: {ACCENT_TINT}; }}"
    'QToolButton#attachButton:pressed, QToolButton#attachButton[active="true"] {'
    f" background: {ACCENT_TINT_ON}; }}"
    "QToolButton#attachButton::menu-indicator { image: none; width: 0; }"
)


_DRAFT_SAVE_MS = 500


_PASTE_TONES = {"service": TAGS["info"], "geometry": TAGS["purple"], "point": FILE_BADGES["image"],
                "table": FILE_BADGES["table"], "text": TAGS["neutral"]}


_CAPABILITIES_MS = 10000
_CAPABILITIES_BYTES = 12 * 1024 * 1024


def _heavier_than(path: str, cap: int) -> bool:
    try:
        return os.path.getsize(path) > cap
    except OSError:
        return False


def _same_attachment(left, right) -> bool:

    if not left or not right:
        return left == right
    try:
        return (os.path.normcase(os.path.abspath(str(left)))
                == os.path.normcase(os.path.abspath(str(right))))
    except (OSError, ValueError):
        return str(left) == str(right)


class _AttachButton(IconButton):


    def __init__(self, parent, tooltip: str):
        super().__init__(parent, tooltip=tooltip, qss=_ATTACH_BTN_QSS)
        self.setObjectName("attachButton")
        self.setStyleSheet(_ATTACH_BTN_QSS)
        self.setFixedSize(_ATTACH_PX, _ATTACH_PX)
        self.ring_radius = _ATTACH_PX / 2
        self.paint_glyph(False)

    def paint_glyph(self, hovered: bool) -> None:
        self.set_icon("plus", _ATTACH_GLYPH, qcolor(INK if hovered else INK_3))

    def enterEvent(self, event):  # noqa: N802
        super().enterEvent(event)
        if self.isEnabled():
            self.paint_glyph(True)

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        self.paint_glyph(False)


class Composer(QFrame):


    send_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()


    queue_clicked = pyqtSignal()
    queue_recall_requested = pyqtSignal()
    files_attached = pyqtSignal(object)
    files_dropped = pyqtSignal(object)
    attachments_changed = pyqtSignal()
    chips_changed = pyqtSignal()
    chip_removed = pyqtSignal(str, str)
    layer_card_clicked = pyqtSignal(str)
    permission_mode_changed = pyqtSignal(str)
    folder_removed = pyqtSignal(str)
    effort_changed = pyqtSignal(str)
    upsell_shown = pyqtSignal(str)
    upgrade_requested = pyqtSignal(str)
    example_chosen = pyqtSignal(str)
    reconnect_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("composer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.setAcceptDrops(False)
        self.setProperty("focused", False)
        self._running = False
        self._sent_at = 0.0

        self._queue_full = False
        self._queued = 0
        self._blocked = False


        self._empty_chat = True
        self._blocked_placeholder = ""
        self._reply_placeholder = ""

        self._run_placeholder = ""


        self._effort_locked = False

        self._compact_controls = 0
        self._last_sent = ""



        self._last_sent_items = []

        self._last_sent_marks: list = []
        self._last_sent_cards: list = []
        self._offline = False



        self._conn_state = "online"




        self._been_online = False


        self._offline_cause = ""


        self._pending_send = False




        self._offline_grace = one_shot(self, self._on_offline_grace_over, _OFFLINE_GRACE_MS)



        self._sticky_hint = False
        self._asked = ""
        self._items: list[dict] = []
        self._tags: dict[str, AttachmentTag] = {}
        self._chips = ChipRow()
        self._cards: dict[tuple, LayerCard] = {}


        self._pastes: list[dict] = []


        self._fresh_paste: tuple | None = None

        self._last_sent_pastes: tuple = ("", [])
        self._attach: AttachPopover | None = None
        self._placeholder = self.tr("Ask or describe a task in QGIS")


        self._draft_key = drafts.NEW_CHAT
        self._draft_tag = account_tag()
        self._draft_between = False
        self._draft_busy = False
        self._draft_timer = one_shot(self, self._save_draft, _DRAFT_SAVE_MS)
        self.destroyed.connect(lambda *_: drafts.flush())
        QTimer.singleShot(0, self._restore_first_draft)

        col = QVBoxLayout(self)
        col.setContentsMargins(_ROW_PAD_X + 2, SPACE_OUTER, _ROW_PAD_X, _ROW_PAD_X)
        col.setSpacing(SPACE_CARD)


        self._selection = SelectionLine(self)
        col.addWidget(self._selection)
        self._selection.left_out.connect(lambda: self._input.setFocus(Qt.FocusReason.OtherFocusReason))

        self._attach_host = QWidget(self)
        self._attach_flow = FlowLayout(self._attach_host, SPACE_CARD, SPACE_TIGHT)
        self._attach_host.hide()
        col.addWidget(self._attach_host)
        self._stale_line = self._build_stale_line()
        col.addWidget(self._stale_line)



        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_OUTER)

        self._attach_btn = _AttachButton(
            self,
            self.tr("Add files or one of this project's layers. A layer can also be"
                    " dragged from the Layers panel; Ctrl+V pastes a picture."))



        self._attach_btn.clicked.connect(self.open_attach)
        row.addWidget(self._attach_btn, 0, Qt.AlignmentFlag.AlignVCenter)


        row.addSpacing(_CHIP_GAP - SPACE_OUTER)
        self._permission_chip = PermissionChip(self)
        self._permission_chip.mode_changed.connect(self.permission_mode_changed.emit)
        self._permission_chip.upgrade_requested.connect(lambda: self.upgrade_requested.emit("autopilot"))
        self._permission_chip.upsell_shown.connect(lambda: self.upsell_shown.emit("autopilot"))
        row.addWidget(self._permission_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self._folder_chip = FolderChip(self)
        self._folder_chip.removed.connect(self.folder_removed.emit)
        row.addWidget(self._folder_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)

        self._input = ComposerInput(self)
        self._input.setPlaceholderText(self._placeholder)
        self._input.set_sheet_anchor(self)
        self._input.submitted.connect(self._on_submit)
        self._input.textChanged.connect(self._sync_send_enabled)
        self._input.textChanged.connect(self._clear_warning)
        self._input.textChanged.connect(self._on_draft_edit)
        self._input.mentions_changed.connect(self._on_mentions_changed)
        self._input.stale_changed.connect(self._sync_stale_line)
        self.chips_changed.connect(self._on_draft_edit)
        self.attachments_changed.connect(self._on_draft_edit)
        self._input.files_requested.connect(self._on_add_files)
        self._input.recall_requested.connect(self._recall_last)
        self._input.image_pasted.connect(lambda image: self.add_image(image, "pasted.png"))
        self._input.paths_pasted.connect(self.add_paths)
        self._input.paste_taker = self._take_paste
        self._input.undo_paste = self._undo_paste
        self._input.installEventFilter(self)
        self._input.sheet().installEventFilter(self)
        col.addWidget(self._input)

        self._effort_chip = EffortChip(self)
        self._effort_chip.effort_changed.connect(self._on_effort_pick)
        self._effort_chip.upgrade_requested.connect(lambda: self.upgrade_requested.emit("effort"))
        self._effort_chip.upsell_shown.connect(lambda: self.upsell_shown.emit("effort"))
        row.addWidget(self._effort_chip, 0, Qt.AlignmentFlag.AlignVCenter)

        self._send_btn = QToolButton(self)
        self._send_btn.setObjectName("sendBtn")
        self._send_btn.setStyleSheet(_BTN_SEND)
        self._send_btn.setFixedSize(_SEND_SIZE, _SEND_SIZE)
        self._send_btn.setIconSize(QSize(_DISC_GLYPH, _DISC_GLYPH))
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)


        QWidget.setTabOrder(self._input, self._attach_btn)
        QWidget.setTabOrder(self._attach_btn, self._permission_chip)
        QWidget.setTabOrder(self._permission_chip, self._effort_chip)
        self._send_btn.setProperty("running", False)
        self._send_btn.setProperty("queue", False)



        self._send_btn.setProperty("offline", False)
        self._send_btn.clicked.connect(self._on_send_or_stop)
        row.addWidget(self._send_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._dictation_ui = ComposerDictation(self, row, col, self._input, self._send_btn)
        QWidget.setTabOrder(self._effort_chip, self._dictation_ui.button)
        self._suggest_ui = ComposerSuggest(self, self._input)

        self._hint = QLabel(self)
        self._hint.setStyleSheet(_HINT_QSS)
        self._hint.setWordWrap(True)
        self._hint.setContentsMargins(2, 0, 0, 0)
        self._hint.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._hint.setOpenExternalLinks(False)
        self._hint.linkActivated.connect(self._on_hint_link)
        self._hint.hide()
        col.addWidget(self._hint)
        self._notice = PromptNotice(self)
        self._notice.send_anyway.connect(self._on_submit)
        col.addWidget(self._notice)
        self._empty_gap = QWidget(self)

        self._empty_gap.setFixedHeight(max(0, EMPTY_CHAT_GAP - col.spacing()))
        col.addWidget(self._empty_gap)
        col.addLayout(row)
        self._hint_timer = one_shot(self, self._passing_line_over)

        self._paint_send()
        self._sync_send_enabled()
        self._sync_controls()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._sync_controls()

    def _chip_width(self, chip, compact: bool) -> int:






        measure = getattr(chip, "width_for", None)
        if callable(measure):
            try:
                return int(measure(compact))
            except (TypeError, RuntimeError):
                pass
        return chip.sizeHint().width()

    def _sync_controls(self) -> None:












        margins = self.contentsMargins()
        room = self.width() - margins.left() - margins.right()
        if room <= 0:
            return
        perm, effort = self._permission_chip, self._effort_chip



        need = (_ATTACH_PX + self._chip_width(perm, False) + _ROW_MIN_GAP
                + self._chip_width(effort, False) + 2 * _SEND_SIZE + 5 * SPACE_OUTER)
        if not self._folder_chip.isHidden():
            need += self._folder_chip.width_for(False) + SPACE_OUTER
        margin = _ROW_HYSTERESIS if self._compact_controls else 0
        compact = not self._empty_chat or need + margin > room
        if compact == bool(self._compact_controls):
            return
        self._compact_controls = int(compact)
        perm.set_compact(compact)
        effort.set_compact(compact)
        self._folder_chip.set_compact(compact)

    def text(self) -> str:

        return splice(self._input.text(), [(p["anchor"].position(), p["raw"]) for p in self._pastes])

    def set_text(self, text: str) -> None:
        self._clear_pastes()
        self._input.set_text(text)

    def _build_stale_line(self) -> QWidget:

        box = QFrame(self)
        box.setObjectName("composerStale")
        box.setStyleSheet(f"#composerStale {{ background: {INSET}; border: 1px solid {LINE};"
                          f" border-radius: {RADIUS_CONTROL}px; }}")
        self._stale_rows = QVBoxLayout(box)
        self._stale_rows.setContentsMargins(SPACE_OUTER, 3, 3, 3)
        self._stale_rows.setSpacing(0)
        box.setToolTip(self.tr("Sent as plain words: the AI agent gets no link to the layer."))
        box.hide()
        return box

    def _stale_row(self, label: str, name: str | None) -> QWidget:
        row = QWidget(self._stale_line)
        row.setMinimumHeight(HIT_PX)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(SPACE_CARD)
        glyph = QLabel(row)
        glyph.setPixmap(icon_for(self, "layers", _STALE_GLYPH, qcolor(RED)).pixmap(_STALE_GLYPH, _STALE_GLYPH))
        line.addWidget(glyph)
        title = QLabel(row)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet(_STALE_NAME_QSS)
        title.ensurePolished()
        title.setText(title.fontMetrics().elidedText(label, Qt.TextElideMode.ElideMiddle, _STALE_NAME_PX))
        title.setToolTip(label)
        line.addWidget(title)
        state = ElidedLabel(self.tr("removed from the project") if name is None
                            else self.tr("renamed {new}").format(new=name), row)
        state.setStyleSheet(_HINT_QSS)
        line.addWidget(state, 1)
        if name is not None:
            use = QPushButton(self.tr("Use"), row)
            use.setStyleSheet(_STALE_BUTTON_QSS)
            use.setFixedHeight(_STALE_BUTTON_PX)
            use.setCursor(Qt.CursorShape.PointingHandCursor)
            use.setToolTip(self.tr("Write {new} in the message instead").format(new=name))
            keyboard_ring(use)
            use.setProperty("staleLabel", label)
            use.clicked.connect(self._adopt_new_name)
            line.addWidget(use)
        return row

    def _sync_stale_line(self) -> None:
        stale = self._input.stale_mentions()
        while self._stale_rows.count():
            widget = self._stale_rows.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        if not stale:
            self._stale_line.hide()
            self._input.setAccessibleDescription("")
            return
        for label, name in stale[:_STALE_ROWS]:
            self._stale_rows.addWidget(self._stale_row(label, name))
        if len(stale) > _STALE_ROWS:
            more = QLabel(self.tr("and {n} more").format(n=len(stale) - _STALE_ROWS), self._stale_line)
            more.setStyleSheet(_HINT_QSS)
            more.setMinimumHeight(HIT_PX)
            self._stale_rows.addWidget(more)

        self._input.setAccessibleDescription("; ".join(
            f"{label}: " + (self.tr("removed from the project") if name is None
                            else self.tr("renamed {new}").format(new=name))
            for label, name in stale))
        self._stale_line.show()

    def _adopt_new_name(self) -> None:

        self._input.adopt_new_names(str(self.sender().property("staleLabel")))
        self.focus_input()

    def _draft_state(self) -> dict:

        files = [str(item["path"]) for item in self._items if item.get("path")]
        images = [{key: item.get(key) for key in ("id", "name", "data_base64", "width", "height")}
                  for item in self._items if item.get("kind") == "image" and not item.get("path")]

        pastes = [{"at": p["anchor"].position(), "text": p["raw"]} for p in self._pastes]
        return {"text": self._input.text(), "chips": self._input.chip_marks(), "cards": self._chips.chips(),
                "pastes": pastes, "files": files, "images": images}

    def _draft_empty(self) -> bool:
        return not (self.text().strip() or self._items or len(self._chips))

    def _on_draft_edit(self) -> None:
        if self._draft_busy:
            return
        if not self._draft_empty():
            self._draft_timer.start()
        else:
            self._draft_timer.stop()
            self._save_draft()

    def _save_draft(self) -> None:
        drafts.put(self._draft_tag, self._draft_key, self._draft_state())

    def _restore_first_draft(self) -> None:

        if self._draft_key != drafts.NEW_CHAT or not self._draft_empty():
            return
        self._show_draft(drafts.get(self._draft_tag, self._draft_key))

    def _show_draft(self, draft: dict) -> None:





        self._draft_busy = True
        try:
            if draft["text"] != self._input.text() or self._input.chip_marks() or self._pastes:
                self.set_text(draft["text"])
            self._input.restore_chips(draft["chips"])
            for paste in draft.get("pastes", ()):
                info = read_paste(paste["text"])
                if info is not None:
                    self._add_paste(paste["text"], info, paste["at"])
            self.clear_chips()
            for card in draft["cards"]:
                self.add_chip(card)
            self.clear_attachments()
            for path in draft["files"]:
                item = attachment_from_path(path)
                if item:
                    self.add_attachment(item)
            for image in draft["images"]:
                self.add_attachment(image)
        finally:
            self._draft_busy = False

    def thread_key(self) -> str:

        if self._draft_between or self._draft_key == drafts.NEW_CHAT:
            return ""
        return self._draft_key

    def bind_draft(self, thread_id) -> None:








        self._draft_timer.stop()
        tag = account_tag()
        if thread_id is None:
            self._save_draft()
            self._draft_between = True
            return
        key = str(thread_id) or drafts.NEW_CHAT
        between, self._draft_between = self._draft_between, False
        if key == self._draft_key and tag == self._draft_tag:
            return
        if between:
            self._draft_tag, self._draft_key = tag, key
            self._show_draft(drafts.get(tag, key))
            self._suggest_ui.refresh()
            return
        drafts.put(self._draft_tag, self._draft_key, "")
        self._draft_tag, self._draft_key = tag, key
        self._save_draft()

    def clear(self) -> None:


        self._pending_send = False
        self._clear_hint()
        self._clear_pastes()
        self._input.clear()

    def focus_input(self) -> None:
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_send_shortcut(self, value: str) -> None:

        self._input.set_send_on_modifier(str(value) == "modifier")
        self._paint_send()

    def _send_help(self) -> str:







        modifier = self.tr("Cmd") if sys.platform == "darwin" else self.tr("Ctrl")
        if self._input.sends_on_modifier():
            return self.tr("Send ({mod}+Enter). Enter for a new line.").format(mod=modifier)
        return self.tr("Send (Enter). Shift+Enter for a new line.")

    def set_empty_chat(self, empty: bool) -> None:






        self._empty_chat = bool(empty)
        self._input.set_min_lines(EMPTY_CHAT_LINES if empty else MIN_LINES)
        self._empty_gap.setVisible(self._empty_chat)
        self._sync_controls()

    def set_mentions(self, items) -> None:
        self._input.set_mentions(items)

    def set_mention_provider(self, provider) -> None:

        self._input.set_mention_provider(provider)

    def set_permission_mode(self, approval: str) -> None:

        self._permission_chip.set_mode(approval)

    def set_effort(self, effort: str) -> None:

        self._effort_chip.set_effort(effort)
        self._sync_effort_lock()

    def effort(self) -> str:
        return self._effort_chip.effort()

    def set_effort_live(self, efforts) -> None:

        self._effort_chip.set_live(efforts)

    def _on_effort_pick(self, effort: str) -> None:
        self._sync_effort_lock()
        self.effort_changed.emit(effort)

    def set_paid_plan(self, paid: bool) -> None:

        self._effort_chip.set_paid(paid)
        self._permission_chip.set_paid(paid)
        self._sync_effort_lock()

    def _sync_effort_lock(self) -> None:





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

    def _apply_placeholder(self) -> None:
        suggest_ui = getattr(self, "_suggest_ui", None)
        if suggest_ui is not None and suggest_ui.predicting():

            self._input.setPlaceholderText("")
        elif self._blocked and self._blocked_placeholder:
            self._input.setPlaceholderText(self._blocked_placeholder)
        elif self._effort_locked and not self._blocked:
            names = {e: n for e, n, _note in effort_texts(self._effort_chip)}
            level = names.get(self._effort_chip.chosen(), "")
            line = self.tr("Pro unlocks {level} effort. Or pick Low.")
            self._input.setPlaceholderText(line.format(level=level))
        elif self._offline and self._been_online and self._hint.isHidden() and not self._running:




            self._input.setPlaceholderText(self.tr("Can't reach TerraLab. Retrying."))
        elif self._reply_placeholder:
            self._input.setPlaceholderText(self._reply_placeholder)
        elif self._running and self._run_placeholder:
            self._input.setPlaceholderText(self._run_placeholder)
        else:
            self._input.setPlaceholderText(self._placeholder)

    def set_reply_placeholder(self, text: str) -> None:

        self._reply_placeholder = text or ""
        self._apply_placeholder()

    def set_run_placeholder(self, text: str) -> None:

        self._run_placeholder = text or ""
        self._apply_placeholder()

    def set_placeholder(self, text: str) -> None:

        self._placeholder = text or ""
        self._apply_placeholder()

    def show_hint(self, text: str, *, sticky: bool = False) -> None:





        self._hint.setStyleSheet(_HINT_QSS)
        self._hint.setText(text)
        self._sticky_hint = bool(sticky)
        self._hint.show()
        self._hint_timer.stop()
        if not sticky:
            self._hint_timer.start(_HINT_MS)

    def show_warning(self, text: str, *, sticky: bool = False,
                     focus: bool = True, link: tuple[str, str] | None = None) -> None:








        if not text:
            return
        self._hint.setStyleSheet(_HINT_WARN_QSS)
        self._hint.setText(self._with_link(text, link))
        self._sticky_hint = bool(sticky)
        self._hint.show()
        self._hint_timer.stop()
        if not sticky:
            self._hint_timer.start(_HINT_WARN_MS)
        self._apply_placeholder()
        if focus:




            self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    @staticmethod
    def _with_link(text: str, link: tuple[str, str] | None) -> str:







        if not link or not link[0] or not link[1]:
            return text
        label, href = link
        return (f'{html.escape(str(text))} <a href="{html.escape(str(href), quote=True)}" '
                f'style="color: {INK}; text-decoration: underline;">{html.escape(str(label))}</a>')

    def drop_hint(self, text: str) -> None:

        if not self._hint.isHidden() and self._hint.text() == text:
            self._passing_line_over()

    def hint_text(self) -> str:
        return self._notice.text() or (self._hint.text() if self._hint.isVisible() else "")

    def _clear_warning(self) -> None:





        self._answer_question("edited")
        if self._sticky_hint:
            if self._pending_send and not (self.text().strip() or self._items):



                self._pending_send = False
                self._clear_hint()
                if self._offline and self._offline_cause:
                    self.show_warning(self._offline_line(), sticky=True, focus=False)
            return
        self._passing_line_over()

    def _passing_line_over(self) -> None:




        self._clear_hint()
        if self._offline and (self._offline_cause or (self._pending_send and not self._in_offline_grace())):
            self.show_warning(self._offline_line(), sticky=True, focus=False)

    def _clear_hint(self) -> None:
        self._sticky_hint = False
        if not self._hint.isHidden():
            self._hint_timer.stop()
            self._hint.hide()
            self._apply_placeholder()

    def examples_dialog(self, place: str = "", opened_from: str = "composer") -> ExamplesDialog:








        dialog = ExamplesDialog(self.window() or self, opened_from=opened_from,
                                place=place or HOME_KEY)
        dialog.prompt_chosen.connect(self.add_prompt)
        dialog.example_chosen.connect(self.example_chosen.emit)
        return dialog

    def open_examples(self, connector: str = "") -> None:









        if self._blocked:
            return
        dialog = self.examples_dialog()
        if connector:
            dialog.open_connector(str(connector))
        try:
            exec_dialog(dialog)
        finally:




            dialog.deleteLater()

    def open_data_sources(self, opened_from: str = "composer") -> None:

        if self._blocked:
            return

        dialog = self.examples_dialog(SOURCES_KEY, opened_from)
        try:
            exec_dialog(dialog)
        finally:
            dialog.deleteLater()

    def add_prompt(self, text: str, chip=None, marks=()) -> None:















        mention = dict(chip) if isinstance(chip, dict) and chip.get("value") else None
        draft = self.text().strip()
        if mention is None:
            base = 0
            if not draft:
                self.set_text(text)
            elif text:
                base = len(self._input.text()) + 2
                self._input.append_text("\n\n" + text, at_end=True)
            if text and marks:
                self._input.restore_chips([dict(m, at=m["at"] + base) for m in marks
                                           if isinstance(m, dict) and isinstance(m.get("at"), int)])
        else:
            if not draft:
                self.set_text("")
            else:
                self._input.append_text("\n\n", at_end=True)
            self.pin_mention(mention)
            self.append_text(text)
        files = chip.get("attach") if isinstance(chip, dict) else None
        for path in files if isinstance(files, list) else ():
            item = attachment_from_path(str(path))
            if item:
                self.add_attachment(item)
        self.focus_input()

    def attach_popover(self) -> AttachPopover:

        if self._attach is None:
            self._attach = AttachPopover(self)
            self._attach.add_files_requested.connect(self._on_add_files)
            self._attach.add_layer_requested.connect(self._input.begin_layer_mention)
            self._attach.sources_requested.connect(self.open_data_sources)
            self._attach.installEventFilter(self)
        return self._attach

    def open_attach(self) -> None:
        if self._blocked:
            return
        self._attach_btn.set_active(True)
        self.attach_popover().set_layers_available(self._input.has_layers())
        self.attach_popover().show_by(self, self.window())

    def attachments(self) -> list:

        return [wire_shape(item) for item in self._items]

    def attachments_over_budget(self) -> dict | None:










        sized = [(len(str(item.get("data_base64") or "")), item)
                 for item in (wire_shape(x) for x in self._items)]

        if sum(size for size, _ in sized) <= limits.current("ATTACHMENTS_TOTAL_BYTES"):
            return None
        return max(sized, key=lambda pair: pair[0])[1]

    def room_left(self) -> int:

        return max(0, MAX_ATTACHMENTS - len(self._items))

    def add_attachment(self, item: dict) -> bool:
        if (not isinstance(item, dict) or item.get("kind") not in ("image", "file", "document")
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
        item = pasted_image_attachment(image, name)
        return self.add_attachment(item) if item else False

    def add_paths(self, paths) -> list:







        accepted = []
        unsupported = []
        unreadable = []
        heavy = []
        room = self.room_left()
        over = 0
        for path in list(paths or []):
            path = str(path)
            if not path or len(path) > 4096:
                continue
            kind = classify(path)
            if os.path.isdir(path):
                unsupported.append(path.replace("\\", "/").split("/")[-1])
                continue
            if len(accepted) >= room:
                over += 1
                continue
            if kind == "document" and _heavier_than(path, MAX_DOCUMENT_FILE_BYTES):

                heavy.append(path.replace("\\", "/").split("/")[-1])
                continue
            item = attachment_from_path(path)
            if item is None:


                unreadable.append(path.replace("\\", "/").split("/")[-1])
                continue
            if self.add_attachment(item):
                accepted.append(path)
        self._report_rejections(unsupported, unreadable, over, heavy)
        return accepted

    def _report_rejections(self, unsupported, unreadable, over: int, heavy=()) -> None:
        line = self._rejection_line(unsupported, unreadable, over, heavy)
        if line:
            self.show_hint(line)

    def _rejection_line(self, unsupported, unreadable, over: int, heavy=()) -> str:

        parts = []
        if unsupported:
            parts.append(self.tr("a folder cannot be added, drop its files: {names}")
                         .format(names=", ".join(unsupported[:3])))
        if heavy:
            parts.append(self.tr("too large, {mb} MB at most: {names}")
                         .format(mb=MAX_DOCUMENT_FILE_BYTES // (1024 * 1024), names=", ".join(list(heavy)[:3])))
        if unreadable:
            parts.append(self.tr("could not be read: {names}")
                         .format(names=", ".join(unreadable[:3])))
        if over:
            parts.append(self.tr("{n} left out, {total} at most")
                         .format(n=over, total=MAX_ATTACHMENTS))
        return "; ".join(parts)

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

    def _take_paste(self, text: str, cursor: QTextCursor) -> bool:

        info = read_paste(text)
        if info is None or self._input.isReadOnly():
            return False
        if cursor.hasSelection():
            cursor.removeSelectedText()
            self._input.setTextCursor(cursor)
        paste = self._add_paste(text, info, cursor.position())
        self._fresh_paste = (paste, self._input.document().revision())
        self._paste_changed()
        too_large = self._too_large()
        if too_large:
            self.show_warning(too_large)
        return True

    def _add_paste(self, text: str, info: dict, position: int) -> dict:
        anchor = QTextCursor(self._input.document())
        anchor.setPosition(max(0, min(int(position), len(self._input.text()))))

        anchor.setKeepPositionOnInsert(True)
        ground, ink = _PASTE_TONES[info["tone"]]
        card = AttachCard(info["name"], info["kind"], pixmap_for(self, info["glyph"], TILE_GLYPH, qcolor(ink)),
                          self._attach_host, closable=True, clickable=True, tooltip=info["tip"],
                          tile_color=ground)

        card.setAccessibleDescription(f"{info['kind']}. {info['tip']}")
        paste = {"raw": text, "anchor": anchor, "card": card, "info": info}
        card.removed.connect(lambda: self._drop_paste(paste))
        card.opened.connect(lambda: self._open_paste(paste))
        self._pastes.append(paste)
        self._attach_flow.addWidget(card)
        self._attach_host.show()
        self._attach_host.updateGeometry()
        self._count_service_layers(paste)
        return paste

    def _count_service_layers(self, paste: dict) -> None:






        url = capabilities_url(paste["raw"].strip(), paste["info"].get("service", ""))
        if not url:
            return
        reply = QgsNetworkAccessManager.instance().get(QNetworkRequest(QUrl(url)))



        reply.setParent(self)
        timer = one_shot(reply, reply.abort, _CAPABILITIES_MS)
        timer.start()

        def progress(received, _total):
            if received > _CAPABILITIES_BYTES:
                reply.abort()

        def finished():
            timer.stop()
            ok = reply.error() == QNetworkReply.NetworkError.NoError
            body = bytes(reply.readAll()) if ok else b""
            reply.deleteLater()
            count = count_layers(paste["info"]["service"], body) if body else 0
            if not count or paste not in self._pastes:
                return
            words = (self.tr("1 layer") if count == 1
                     else self.tr("{n} layers").format(n=QLocale().toString(count)))
            paste["info"]["kind"] = f"{paste['info']['kind']} · {words}"
            paste["card"].set_kind(paste["info"]["kind"])
            paste["card"].updateGeometry()

        reply.downloadProgress.connect(progress)
        reply.finished.connect(finished)

    def _undo_paste(self) -> bool:
        paste, revision = self._fresh_paste or (None, -1)
        self._fresh_paste = None
        if paste not in self._pastes or revision != self._input.document().revision():
            return False
        self._paste_as_text(paste)
        return True

    def _paste_as_text(self, paste: dict) -> None:

        if paste not in self._pastes:
            return
        position = paste["anchor"].position()
        self._remove_paste_card(paste)
        typed = self._input.text()

        before, after = typed[position - 1:position], typed[position:position + 1]
        words = splice(before + after, [(len(before), paste["raw"])])[len(before):]
        words = words[:len(words) - len(after)] if after else words
        cursor = QTextCursor(self._input.document())
        cursor.setPosition(position)
        cursor.insertText(words, QTextCharFormat())
        self._input.setTextCursor(cursor)
        self._paste_changed()
        self.focus_input()

    def _drop_paste(self, paste: dict) -> None:
        self._remove_paste_card(paste)
        self._paste_changed()
        self.focus_input()

    def _open_paste(self, paste: dict) -> None:
        info = paste["info"]
        ground, ink = _PASTE_TONES[info["tone"]]
        preview = FilePreview({"name": info["name"], "text": paste["raw"], "meta": info["kind"],
                               "mono": info["mono"],
                               "tile": (pixmap_for(self, info["glyph"], 16, qcolor(ink)), ground)}, self)
        if self._paste_place(paste) is not None:
            preview.add_button(self.tr("Show on map"), lambda: self._show_paste_on_map(paste))
        preview.add_button(self.tr("Edit as text"), lambda: self._paste_as_text(paste))
        preview.open_over_window()

    def _paste_place(self, paste: dict):

        if paste["info"]["tone"] not in ("point", "geometry"):
            return None
        try:
            from qgis.utils import iface
        except ImportError:
            return None
        found = place(paste["raw"]) if iface is not None else None
        if found is None or found[0].isNull() or found[0].isEmpty():
            return None
        geometry, authid = found
        canvas_crs = iface.mapCanvas().mapSettings().destinationCrs()
        if not authid:


            return (geometry, canvas_crs) if not canvas_crs.isGeographic() else None
        geometry = QgsGeometry(geometry)
        try:
            transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem(authid), canvas_crs,
                                               QgsProject.instance())
            geometry.transform(transform)
        except QgsCsException:
            return None
        return geometry, canvas_crs

    def _show_paste_on_map(self, paste: dict) -> None:

        found = self._paste_place(paste)
        if found is None:
            return
        from qgis.utils import iface

        geometry, crs = found
        canvas = iface.mapCanvas()
        box = geometry.boundingBox()
        if box.width() == 0 and box.height() == 0:
            if canvas.extent().isEmpty():

                box.grow(0.01 if crs.isGeographic() else 1000)
                canvas.setExtent(box)
            else:
                canvas.setCenter(box.center())
        else:
            box.scale(1.2)
            canvas.setExtent(box)
        canvas.refresh()
        canvas.flashGeometries([geometry], crs)

    def _remove_paste_card(self, paste: dict) -> None:
        if paste in self._pastes:
            self._pastes.remove(paste)
        card = paste["card"]
        self._attach_flow.removeWidget(card)
        card.deleteLater()
        self._sync_row()

    def _clear_pastes(self) -> None:

        self._fresh_paste = None
        for paste in list(self._pastes):
            self._remove_paste_card(paste)

    def _paste_changed(self) -> None:
        self._sync_send_enabled()
        self._on_draft_edit()

    def _sync_row(self) -> None:
        self._attach_host.setVisible(bool(self._items) or bool(len(self._chips)) or bool(self._pastes))
        self._attach_host.updateGeometry()

    def chips(self) -> list:








        out = live_layer_chips(self._chips.chips(), layer_name)
        seen = {ChipRow.key(chip) for chip in out}

        for chip in live_mentions(self._input.mentions(), layer_name):
            if ChipRow.key(chip) not in seen:
                out.append(chip)
        return out

    def append_text(self, text: str) -> None:

        self._input.append_text(text)

    def pin_mention(self, chip: dict) -> bool:





        return self._input.insert_chip(chip)

    def _on_mentions_changed(self) -> None:
        self.chips_changed.emit()
        self._sync_send_enabled()

    def add_chip(self, chip: dict) -> bool:

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

    def peek_chips(self) -> list:






        return self.chips()

    def sent_marks(self) -> list:



        if self._pastes:
            return []
        full = self._input.text()
        lead = len(full) - len(full.lstrip())
        return [dict(m, at=m["at"] - lead) for m in self._input.chip_marks() if m["at"] >= lead]

    def take_chips(self) -> list:






        chips = self.chips()
        self.clear_chips()
        return chips

    def _on_add_files(self) -> None:



        QTimer.singleShot(0, self._pick_files)

    def _pick_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, self.tr("Add files from your computer"), "", any_filter())
        self._attach_picked(paths)

    def _attach_picked(self, paths) -> None:
        accepted = self.add_paths(list(paths or []))
        if accepted:
            self.files_attached.emit(accepted)
            self.focus_input()

    def set_running(self, running: bool) -> None:
        ended = self._running and not running
        self._running = bool(running)
        self._send_btn.setProperty("running", self._running)
        repolish(self._send_btn)
        self._paint_send()
        self._sync_send_enabled()
        self._sync_effort_run_lock()
        if self._running:
            self._suggest_ui.forget_prediction()
            self._suggest_ui.forget_rows()
        elif ended:
            self._suggest_ui.prepare()
        self._apply_placeholder()

    def is_running(self) -> bool:
        return self._running

    def selection_snapshot(self) -> dict | None:

        return self._selection.snapshot()

    def selection_sent(self) -> None:
        self._selection.sent()

    def unwire_selection(self) -> None:
        self._selection.unwire()

    def set_queue_count(self, count: int, cap: int) -> None:


        self._queued = max(0, int(count))
        self._queue_full = self._queued >= int(cap)
        self._paint_send()
        self._sync_send_enabled()

    def queues(self) -> bool:

        return self._running and not self._blocked and bool(self.text().strip())

    def set_blocked(self, blocked: bool, placeholder: str = "") -> None:

        self._blocked = bool(blocked)
        self._blocked_placeholder = placeholder or ""
        self._input.setReadOnly(self._blocked or self._effort_locked)
        self._apply_placeholder()
        self._attach_btn.setEnabled(not (self._blocked or self._effort_locked))
        self._permission_chip.setEnabled(not self._blocked)
        self._dictation_ui.sync()
        self._sync_effort_run_lock()
        self._paint_send()
        self._sync_send_enabled()

    def _sync_effort_run_lock(self) -> None:




        self._effort_chip.set_running(self._running and not self._blocked)
        self._effort_chip.setEnabled(not self._blocked)

    def is_blocked(self) -> bool:
        return self._blocked

    def set_offline(self, offline: bool, state: str = "", cause: str | None = None) -> None:








        was_offline = self._offline
        had_cause = self._offline_cause
        self._offline = bool(offline)
        if not self._offline:
            self._offline_cause = ""
            self._been_online = True
        elif cause is not None:
            self._offline_cause = str(cause)
        if self._offline and not was_offline:
            self._offline_grace.start()
        elif not self._offline:
            self._offline_grace.stop()
        self._conn_state = str(state or ("offline" if offline else "online"))
        if self._send_btn.property("offline") != self._offline:
            self._send_btn.setProperty("offline", self._offline)
            repolish(self._send_btn)
        self._sync_send_enabled()
        self._paint_send()
        self._apply_placeholder()



        self._sync_effort_run_lock()
        if self._offline:



            if self._offline_cause or (self._pending_send and not self._in_offline_grace()):
                self.show_warning(self._offline_line(), sticky=True, focus=False)
            elif had_cause and self._sticky_hint:
                self._clear_hint()
            return
        if was_offline and self._pending_send:
            self._flush_pending_send()
        elif was_offline and self._sticky_hint:
            self._clear_hint()

    def _in_offline_grace(self) -> bool:



        if not self._offline:
            return False
        if self._offline_grace.isActive():
            return True
        return not self._been_online and self._conn_state == "connecting" and not self._offline_cause

    def _on_offline_grace_over(self) -> None:

        if not self._offline:
            return
        self._paint_send()
        if self._pending_send and not self._in_offline_grace():
            self.show_warning(self._offline_line(), sticky=True, focus=False)

    def _offline_line(self) -> str:

        if self._in_offline_grace():
            return self.tr("Connecting to TerraLab. Your message goes out as soon as the connection is up.")
        if self._offline_cause:
            if not self._pending_send:
                return self._offline_cause
            return self._offline_cause + " " + self.tr(
                "Your message stays here and goes out as soon as the connection is back.")
        return self.tr("Can't reach TerraLab. Retrying. Your message stays here and goes "
                       "out as soon as the connection is back.")

    def _flush_pending_send(self) -> None:





        self._pending_send = False
        self._clear_hint()
        if self._running or self._blocked or self._effort_locked:
            return
        if not (self.text().strip() or self._items):
            return


        self._submit(check_quality=False)
        self.show_hint(self.tr("Connection is back. Your message was sent."))

    def connection_state(self) -> str:
        return self._conn_state

    def set_dictation(self, dictation) -> None:

        self._dictation_ui.attach(dictation)

    def set_suggester(self, suggester) -> None:

        self._suggest_ui.attach(suggester)

    def suggestion_drawn(self) -> None:

        self._apply_placeholder()

    def suggest_quiet(self) -> bool:

        return self._running or self._blocked or self._offline or bool(self._pastes) or self._dictation_ui.active

    def dictation_blocked(self) -> bool:
        return self._blocked

    def dictation_clear_hint(self) -> None:
        self._clear_hint()

    def dictation_warn(self, text: str, link: tuple[str, str] | None) -> None:
        self.show_warning(text, link=link)

    def dictation_state_changed(self) -> None:
        on_clip = self._dictation_ui.active

        for control in (self._attach_btn, self._permission_chip, self._effort_chip):
            control.setVisible(not on_clip)
        if not on_clip:
            self._set_disc_state(self._running, self.queues(), self._offline)
        self._paint_send()
        self._sync_send_enabled()

    def _set_disc_state(self, running: bool, queue: bool, offline: bool) -> None:
        changed = False
        for name, value in (("running", running), ("queue", queue), ("offline", offline)):
            if bool(self._send_btn.property(name)) != bool(value):
                self._send_btn.setProperty(name, bool(value))
                changed = True
        if changed:
            repolish(self._send_btn)

    def _paint_send(self) -> None:
        if self._dictation_ui.active:

            paper = paper_of(self._send_btn)
            self._set_disc_state(False, False, False)
            self._send_btn.setIcon(icon_for(self._send_btn, "lu.check", _DISC_GLYPH, QColor(ON_ACCENT), paper))
            self._send_btn.setToolTip(self.tr("Done: write what I said (Enter)"))
            self._send_btn.setAccessibleName(self.tr("Finish dictation"))
            return




        paper = paper_of(self._send_btn)
        disabled = paper
        queue = self.queues()
        if self._send_btn.property("queue") != queue:
            self._send_btn.setProperty("queue", queue)
            repolish(self._send_btn)
        if queue:
            self._send_btn.setIcon(icon_for(self._send_btn, "lu.corner-down-left", _DISC_GLYPH, qcolor(INK)))
            self._send_btn.setToolTip(
                self.tr("The queue holds 5 messages. Send or remove one first.") if self._queue_full else
                self.tr("Queue it: the AI agent reads it at its next step (Enter)"))
            self._send_btn.setAccessibleName(self.tr("Queue"))
        elif self._running:


            self._send_btn.setIcon(icon_for(self._send_btn, "stop", 14, qcolor(INK)))
            self._send_btn.setToolTip(self.tr("Stop the run"))
            self._send_btn.setAccessibleName(self.tr("Stop"))
        elif self._effort_locked and not self._blocked:


            self._send_btn.setIcon(icon_for(self._send_btn, "lock", _DISC_GLYPH, QColor(ON_ACCENT), disabled))
            self._send_btn.setToolTip(self.tr("Unlock this effort level with Pro."))
            self._send_btn.setAccessibleName(self.tr("Unlock with Pro"))
        elif self._offline and not self._in_offline_grace():




            self._send_btn.setIcon(
                icon_for(self._send_btn, "lu.refresh-cw", _DISC_GLYPH, QColor(ON_ACCENT), disabled))
            self._send_btn.setToolTip(
                self.tr("Can't reach TerraLab. Press to retry now; your message is kept."))
            self._send_btn.setAccessibleName(self.tr("Retry the connection"))
        else:
            self._send_btn.setIcon(
                icon_for(self._send_btn, "arrow_up", _DISC_GLYPH, QColor(ON_ACCENT), disabled))
            self._send_btn.setToolTip(self.tr("Connecting to TerraLab...") if self._in_offline_grace()
                                      else self._send_help())
            self._send_btn.setAccessibleName(self.tr("Send"))

    def _sync_send_enabled(self) -> None:
        if self._dictation_ui.active:
            self._send_btn.setEnabled(self._dictation_ui.state == "recording")
            return
        if self._running:
            if bool(self._send_btn.property("queue")) != self.queues():
                self._paint_send()
            self._send_btn.setEnabled(not (self.queues() and self._queue_full))
            return




        if self._blocked:
            self._send_btn.setEnabled(False)
            return
        if self._effort_locked:
            self._send_btn.setEnabled(True)
            return
        if self._offline:



            self._send_btn.setEnabled(True)
            return
        self._send_btn.setEnabled(bool(self.text().strip()) or bool(self._items))

    def _on_submit(self) -> None:
        self._submit(check_quality=True)

    def _submit(self, *, check_quality: bool) -> None:
        if self._running:
            if self.queues() and not self._queue_full:
                too_large = self._too_large()
                if too_large:
                    self.show_warning(too_large)
                    return
                self.queue_clicked.emit()
            return
        if self._blocked:
            return
        if self._offline and not self._effort_locked:




            if self.text().strip() or self._items:
                self._pending_send = True
            if self._in_offline_grace():



                if self._pending_send:
                    self.show_hint(self._offline_line(), sticky=True)
                self.reconnect_requested.emit()
                return
            self.show_warning(self._offline_line() if self._pending_send else
                              self.tr("Can't reach TerraLab. Retrying now."),
                              sticky=True)
            self.reconnect_requested.emit()
            return
        if self._effort_locked:
            self.upgrade_requested.emit("effort")
            return
        if not (self.text().strip() or self._items):
            return



        too_large = self._too_large()
        if too_large:
            self.show_warning(too_large)
            return
        if check_quality:






            asked = not self._notice.isHidden()
            self._answer_question("sent")
            reason = "" if asked else check_prompt(
                self.text(), len(self._items), len(self.chips()), first_message=self._empty_chat,
                known_names=tuple(layer.name() for layer in QgsProject.instance().mapLayers().values()))
            if reason:
                self._ask_before_sending(reason)
                return
        self._last_sent = self.text()
        self._last_sent_pastes = (self._input.text(), [(p["anchor"].position(), p["raw"]) for p in self._pastes])
        self._last_sent_items = [dict(item) for item in self._items]

        self._last_sent_marks = self._input.chip_marks()
        self._last_sent_cards = self._chips.chips()
        self._sent_at = time.monotonic()
        trace.mark("send.click")
        self.send_clicked.emit()

    def _recall_last(self) -> None:
        if self._queued and not self._input.isReadOnly() and not self.text():
            self.queue_recall_requested.emit()
            return



        if self._input.isReadOnly() or self._blocked or not self._last_sent or self._pastes:
            return
        typed, pastes = self._last_sent_pastes
        if pastes and splice(typed, pastes) == self._last_sent:

            self.set_text(typed)
            self._input.restore_chips(self._last_sent_marks)
            for position, raw in pastes:
                info = read_paste(raw)
                if info is not None:
                    self._add_paste(raw, info, position)
            self._paste_changed()
        else:
            self.set_text(self._last_sent)
            self._input.restore_chips(self._last_sent_marks)
        for card in self._last_sent_cards:
            self.add_chip(dict(card))
        for item in self._last_sent_items:
            self.add_attachment(dict(item))

    def remember_sent(self, text: str, items=(), marks=()) -> None:

        self._last_sent = str(text or "")
        self._last_sent_pastes = ("", [])
        self._last_sent_items = [dict(item) for item in items or [] if isinstance(item, dict)]
        self._last_sent_marks = [dict(m) for m in marks or () if isinstance(m, dict)]
        self._last_sent_cards = []

    def forget_last_sent(self) -> None:

        self._last_sent = ""
        self._last_sent_pastes = ("", [])
        self._last_sent_items = []
        self._last_sent_marks = []
        self._last_sent_cards = []

    def _on_hint_link(self, href: str) -> None:

        if self._dictation_ui.follow_link(href):
            self._clear_hint()

    def _ask_before_sending(self, reason: str) -> None:




        self._clear_hint()
        self._asked = reason
        telemetry.track(ev.PROMPT_QUESTION_SHOWN, {"reason": reason, "first_message": self._empty_chat})
        self._notice.ask(self.tr("Send this as it is?"), self._refusal(reason), self.tr("Send anyway"))
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _answer_question(self, answer: str) -> None:
        if not self._notice.isHidden():
            self._notice.hide()
            telemetry.track(ev.PROMPT_QUESTION_ANSWERED, {"reason": self._asked, "answer": answer})

    def _too_large(self) -> str:





        cap = int(limits.current("PROMPT_MAX_CHARS"))
        length = len(self.text().strip())
        if length > cap:
            return self.tr("This message is {n} characters long, over the {cap} one message can carry. "
                           "Shorten it.").format(n=QLocale().toString(length), cap=QLocale().toString(cap))
        heaviest = self.attachments_over_budget()
        if heaviest is not None:
            return self.tr("These attachments are too large to send in one message. Remove {name} "
                           "or another one.").format(name=heaviest.get("name") or self.tr("the largest one"))
        return ""

    def _refusal(self, reason: str) -> str:
        if reason == NOT_A_TASK:
            return self.tr("It looks like a typing slip. Edit it, or press Send again.")
        if reason == TOO_SHORT:
            return self.tr("A few more words, please: what to do, and on which layer.")
        return ""

    def _on_send_or_stop(self) -> None:
        if self._dictation_ui.active:
            self._dictation_ui.confirm()
        elif self._running and not self.queues():


            if time.monotonic() - self._sent_at >= STOP_AFTER_SEND_S:
                self.stop_clicked.emit()
        else:
            self._on_submit()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._input.sheet() and event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self.show_focus()
        elif watched is self._input:
            if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self.show_focus()
            elif (event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride)
                  and event.key() == Qt.Key.Key_D
                  and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):

                if event.type() == QEvent.Type.KeyPress:
                    self._dictation_ui.toggle()
                event.accept()
                return True
        elif watched is self._attach and event.type() == QEvent.Type.Hide:
            self._attach_btn.set_hovered(False)
            self._attach_btn.set_active(False)
            settle_hover(self._attach_btn)
            self._attach_btn.paint_glyph(self._attach_btn.underMouse())
        return super().eventFilter(watched, event)

    def show_focus(self) -> None:



        self._set_flag("focused", self._input.hasFocus() or self._input.sheet().isVisible())

    def mousePressEvent(self, event):  # noqa: N802

        if event.button() == Qt.MouseButton.LeftButton and self._input.isEnabled():
            self._input.setFocus(Qt.FocusReason.MouseFocusReason)
            self._input.moveCursor(QTextCursor.MoveOperation.End)
            event.accept()
            return
        super().mousePressEvent(event)

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


        if mime is None:
            return False
        return bool(mime_has_layers(mime) or cls.local_paths(mime) or mime.hasImage()
                    or (mime.hasText() and mime.text().strip()))

    def take_drop(self, mime) -> bool:

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
        if mime is not None and mime.hasText() and mime.text().strip() and not self._input.isReadOnly():


            words = QMimeData()
            words.setText(mime.text())
            cursor = self._input.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._input.setTextCursor(cursor)
            self._input.insertFromMimeData(words)
            self.focus_input()
            return True
        return False
