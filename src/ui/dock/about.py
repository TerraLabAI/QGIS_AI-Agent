# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QPixmap
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

from ...core.host_platform import plugin_version
from ...core.served_state import SUPPORT_EMAIL
from ..external_links import open_external_url
from ..font_scale import apply_font_scale_to_tree, scale_px_length, scale_qss_font_px, widget_pixel_ratio
from ..shared import (
    PLUGIN_ICON_PATH,
    PRODUCT_NAME,
    exec_dialog,
    size_within_screen,
    tr,
)
from ..style import (
    _BTN_GHOST,
    _BTN_PRIMARY,
    _BTN_PRIMARY_WIDE,
    BTN_PILL_PX,
    BTN_PRIMARY_WIDE_PX,
    FIELD,
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    INK_3,
    LINE,
    PAGE,
    RADIUS_CARD,
    SURFACE,
    WEIGHT_MEDIUM,
    keyboard_ring,
)
from ..styles import (
    _UPDATE_CARD_STYLE,
    _UPDATE_LATER_STYLE,
    _UPDATE_NOTE_STYLE,
    _UPDATE_TITLE_STYLE,
)


class UpdateBanner(QWidget):




    update_clicked = pyqtSignal(str)
    download_clicked = pyqtSignal(str)
    dismissed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.version = ""
        self._download = False



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
        keyboard_ring(self._update_btn)
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

        self.version = version or ""
        self._download = False
        self._update_btn.setText(self.tr("Update now"))
        self._title.setText(self.tr("{product} {version} is out").format(
            product=PRODUCT_NAME, version=self.version))
        self._note.setText(note or "")
        self._note.setVisible(bool(note))
        self.setVisible(bool(self.version))

    def offer_download(self, version: str) -> None:


        self.offer(version, self.tr("This QGIS has no plugin repository turned on. Download the ZIP, "
                                    "then use Install from ZIP in Plugins > Manage and Install Plugins."))
        self._download = True
        self._update_btn.setText(self.tr("Download"))

    def _on_update(self) -> None:
        if self._download:
            from ...core.plugin_release import PLUGIN_PAGE_URL

            open_external_url(PLUGIN_PAGE_URL, parent=self)
            self.download_clicked.emit(self.version)
            return
        self.update_clicked.emit(self.version)

    def set_busy(self, busy: bool) -> None:

        self._update_btn.setText(self.tr("Updating\u2026") if busy else self.tr("Update now"))
        self._update_btn.setEnabled(not busy)
        self._later_btn.setEnabled(not busy)

    def _on_later(self) -> None:
        self.hide()
        self.dismissed.emit(self.version)


CONTACT_QSS = scale_qss_font_px(
    f"QDialog#contactDialog, QDialog#feedbackDialog {{ background: {PAGE}; }}"
    f"QLabel#ctTitle {{ font-size: 18px; font-weight: 600; color: {INK}; background: transparent; }}"
    f"QLabel#ctSub {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; }}"
    f"QFrame#ctCard {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS_CARD}px; }}"
    f"QLabel#ctTopic {{ font-size: {FONT_HINT}px; font-weight: {WEIGHT_MEDIUM}; color: {INK_2};"
    f" background: {FIELD}; border: 1px solid {LINE}; border-radius: 8px; padding: 2px 9px; }}"
    f"QLabel#ctEmail {{ font-size: 15px; font-weight: 600; color: {INK}; background: transparent; }}"
    f"QLabel#ctNote {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; }}"
    f"QFrame#ctRule {{ background: {LINE}; border: none; min-height: 1px; max-height: 1px; }}"
)


def show_contact_dialog(parent=None, support_email: str = SUPPORT_EMAIL,
                        call_url: str = "") -> None:


    from ...core.host_platform import prefers_reduced_motion
    from ..mascot import GAZE, LIVE, Mascot
    from ..settings_final import InfoButton

    dlg = QDialog(parent)
    dlg.setObjectName("contactDialog")
    dlg.setWindowTitle(tr("Contact us"))
    dlg.setStyleSheet(CONTACT_QSS)
    lay = QVBoxLayout(dlg)
    lay.setSpacing(14)
    lay.setContentsMargins(24, 22, 24, 22)

    head = QHBoxLayout()
    head.setSpacing(14)
    seat = QWidget(dlg)
    seat.setFixedSize(scale_px_length(46), scale_px_length(54))
    mark = Mascot(seat)
    mark.set_mode(GAZE if prefers_reduced_motion() else LIVE)
    mark.set_mood("listen")
    head.addWidget(seat, 0, Qt.AlignmentFlag.AlignVCenter)
    words = QVBoxLayout()
    words.setSpacing(2)
    title = QLabel(tr("Contact us"), dlg)
    title.setObjectName("ctTitle")
    words.addWidget(title)
    sub = QLabel(tr("We read every message."), dlg)
    sub.setObjectName("ctSub")
    words.addWidget(sub)
    head.addLayout(words, 1)
    lay.addLayout(head)

    card = QFrame(dlg)
    card.setObjectName("ctCard")
    inner = QVBoxLayout(card)
    inner.setContentsMargins(18, 16, 18, 16)
    inner.setSpacing(14)
    topics = QHBoxLayout()
    topics.setSpacing(6)
    for word in (tr("Bugs"), tr("Questions"), tr("Ideas"), tr("Team needs")):
        chip = QLabel(word, card)
        chip.setObjectName("ctTopic")
        topics.addWidget(chip)
    topics.addWidget(InfoButton(card, tr("Licences, quotes and invoices for your team, too."), tr("Team needs")),
                     0, Qt.AlignmentFlag.AlignVCenter)
    topics.addStretch(1)
    inner.addLayout(topics)

    line = QHBoxLayout()
    line.setSpacing(12)
    address = QVBoxLayout()
    address.setSpacing(2)
    email_label = QLabel(support_email, card)
    email_label.setObjectName("ctEmail")
    email_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    address.addWidget(email_label)
    version = plugin_version()
    if version:
        version_label = QLabel(tr("{product} {version}").format(product=PRODUCT_NAME, version=version), card)
        version_label.setObjectName("ctNote")
        address.addWidget(version_label)
    line.addLayout(address, 1)
    copy_btn = QPushButton(tr("Copy email address"), card)
    copy_btn.setStyleSheet(_BTN_PRIMARY)
    keyboard_ring(copy_btn)
    copy_btn.setFixedHeight(BTN_PILL_PX)
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_btn.setAutoDefault(False)

    def _copy() -> None:
        QApplication.clipboard().setText(support_email)
        copy_btn.setText(tr("Copied"))
        mark.fire("run.done")

    copy_btn.clicked.connect(_copy)
    line.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignVCenter)
    inner.addLayout(line)

    if call_url:
        rule = QFrame(card)
        rule.setObjectName("ctRule")
        inner.addWidget(rule)
        team = QHBoxLayout()
        team.setSpacing(12)
        note = QLabel(tr("For a team, a call is quickest."), card)
        note.setObjectName("ctSub")
        note.setWordWrap(True)
        team.addWidget(note, 1)
        call_btn = QPushButton(tr("Book a video call"), card)
        call_btn.setStyleSheet(_BTN_GHOST)
        keyboard_ring(call_btn)
        call_btn.setFixedHeight(BTN_PILL_PX)
        call_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        call_btn.setAutoDefault(False)
        call_btn.clicked.connect(lambda: open_external_url(call_url, parent=dlg))
        team.addWidget(call_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        inner.addLayout(team)
    lay.addWidget(card)

    apply_font_scale_to_tree(dlg)
    dlg.adjustSize()
    size_within_screen(dlg, max(dlg.width(), scale_px_length(520)), dlg.height(), scale_px_length(420), 0)
    mark.put(seat.width() / 2.0, seat.height() - 2.0, scale_px_length(44))
    exec_dialog(dlg)
