# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import improve, telemetry
from ..core.served_state import (
    get_privacy_notice_checks,
    get_privacy_notice_heading,
    get_privacy_notice_toggle,
    get_privacy_url,
    get_terms_url,
)
from . import style as S
from .external_links import open_external_url
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .settings_pages import PRIMARY_BTN_QSS, SettingGroup, SettingRow, Switch
from .shared import ICONS_DIR, available_screen_rect, tr

DIALOG_W = 500
SIDE_PAD = 24
MARK_PX = 44
CHECK_PX = 16
FOOTER_TEXT_W = 300


def _load_mark(widget, size: int) -> QPixmap | None:






    for name in ("icon@4x.png", "icon.png"):
        pixmap = QPixmap(os.path.join(ICONS_DIR, name))
        if pixmap.isNull():
            continue
        ratio = widget.devicePixelRatioF() or 1.0
        scaled = pixmap.scaled(
            int(size * ratio),
            int(size * ratio),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(ratio)
        return scaled
    return None


class PrivacyNoticeDialog(QDialog):






    chosen = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("privacyNoticeDialog")
        self.setWindowTitle(tr("Before you start"))
        self.setModal(True)






        available = available_screen_rect(self)
        cap_w = (available.width() - 48) if available is not None else scale_px_length(DIALOG_W)
        cap_h = (available.height() - 48) if available is not None else 0
        self._width = max(320, min(scale_px_length(DIALOG_W), cap_w))
        self._text_width = self._width - 2 * SIDE_PAD - CHECK_PX - 12
        self.setFixedWidth(self._width)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._build_header())



        rows = QWidget(self)
        body = QVBoxLayout(rows)
        body.setContentsMargins(24, 4, 24, 18)
        body.setSpacing(9)
        for claim in self._checks():
            body.addWidget(self._build_check(claim))
        body.addSpacing(6)
        self._improve = self._build_choice(body, rows)
        scroll = QScrollArea(self)
        scroll.setWidget(rows)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        layout.addWidget(scroll, 1)
        if cap_h:
            self.setMaximumHeight(cap_h)

        rule = QFrame(self)
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"border: none; background: {S.LINE};")
        layout.addWidget(rule)

        footer = QHBoxLayout()
        footer.setContentsMargins(24, 13, 24, 15)
        footer.setSpacing(14)
        self._fill_footer(footer)
        layout.addLayout(footer)

    def _build_header(self) -> QHBoxLayout:






        header = QHBoxLayout()
        header.setContentsMargins(24, 22, 24, 12)
        header.setSpacing(14)

        mark = QLabel(self)
        mark.setObjectName("privacyNoticeMark")
        mark.setFixedSize(MARK_PX, MARK_PX)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = _load_mark(self, MARK_PX)
        if pixmap is not None:
            mark.setPixmap(pixmap)
        header.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)

        heading = QLabel(get_privacy_notice_heading(tr("AI Agent and your data")), self)
        heading.setWordWrap(True)
        heading.setStyleSheet(scale_qss_font_px(f"font-size: 17px; font-weight: 600; color: {S.INK};"))
        header.addWidget(heading, 1, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _checks(self) -> tuple:




        return get_privacy_notice_checks((
            tr("Your chats are private"),
            tr("Hosted in the EU"),
            tr("Never used to train AI"),
            tr("Deleted after 90 days at most"),
        ))

    def _build_check(self, claim: str) -> QWidget:
        row = QWidget(self)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(12)
        tick = QLabel(row)
        tick.setFixedSize(CHECK_PX, CHECK_PX)
        tick.setPixmap(pixmap_for(tick, "lu.check", CHECK_PX, QColor(S.accent_ink())))
        line.addWidget(tick, 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel(claim, row)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setStyleSheet(scale_qss_font_px(f"font-size: 13px; font-weight: 600; color: {S.INK};"))
        text.setFixedWidth(self._text_width)
        line.addWidget(text, 1)
        return row

    def _build_choice(self, body: QVBoxLayout, parent: QWidget) -> Switch:

        title, note = get_privacy_notice_toggle(
            tr("Help improve AI Agent"),
            tr("Your usage and chats help us fix bugs. Turn it off anytime."))
        group = SettingGroup(parent)
        group.setObjectName("privacyNoticeChoices")
        group.setStyleSheet(
            f"QFrame#privacyNoticeChoices {{ border: 1px solid {S.LINE}; border-radius: 10px;"
            " background: transparent; }"
            "QFrame#privacyNoticeChoices > QFrame#settingsRow { border: none; background: transparent; }")
        on = telemetry.is_telemetry_enabled() and improve.is_improve_enabled()
        switch = Switch(group, bool(on))
        group.add_row(SettingRow(title, note, switch, group))
        body.addWidget(group)
        return switch

    def choice(self) -> bool:

        return bool(self._improve.isChecked())

    def accept(self) -> None:
        self.chosen.emit(self.choice())
        super().accept()

    def _fill_footer(self, footer: QHBoxLayout) -> None:



        terms_link = f'<a style="color:{S.accent_ink()};" href="{get_terms_url()}">{tr("Terms")}</a>'
        privacy_link = f'<a style="color:{S.accent_ink()};" href="{get_privacy_url()}">{tr("Privacy Policy")}</a>'




        body = tr("Continuing accepts the {terms} and the {privacy}.")
        for token, value in (("{terms}", terms_link), ("{privacy}", privacy_link)):
            body = body.replace(token, value)
        accept_line = QLabel(body, self)
        accept_line.setObjectName("privacyNoticeAcceptLine")
        accept_line.setWordWrap(True)
        accept_line.setTextFormat(Qt.TextFormat.RichText)
        accept_line.setStyleSheet(scale_qss_font_px(f"font-size: 11px; color: {S.INK_3};"))
        accept_line.setMaximumWidth(FOOTER_TEXT_W)






        accept_line.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)


        accept_line.setOpenExternalLinks(False)
        accept_line.linkActivated.connect(lambda url: open_external_url(url, parent=self))
        footer.addWidget(accept_line, 1)

        self._continue = QPushButton(tr("Continue"), self)
        self._continue.setObjectName("privacyNoticeContinue")
        self._continue.setStyleSheet(PRIMARY_BTN_QSS)
        self._continue.setCursor(Qt.CursorShape.PointingHandCursor)
        self._continue.setDefault(True)
        self._continue.clicked.connect(self.accept)
        footer.addWidget(self._continue, 0, Qt.AlignmentFlag.AlignVCenter)


        self.setTabOrder(self._continue, accept_line)
        self._continue.setFocus()
