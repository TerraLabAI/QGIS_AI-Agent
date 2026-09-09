# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The first-run privacy notice, opened at the first send and nowhere else."""















from __future__ import annotations

import html
import os

from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QPixmap
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

from . import style as S
from .external_links import open_external_url
from .font_scale import scale_px_length
from .settings_pages import PRIMARY_BTN_QSS
from .shared import (
    ICONS_DIR,
    available_screen_rect,
    get_pricing_url,
    get_privacy_notice_heading,
    get_privacy_notice_rows,
    get_privacy_url,
    get_terms_url,
    tr,
)

DIALOG_W = 500
SIDE_PAD = 24
MARK_PX = 52
BADGE_PX = 34
GLYPH_PX = 19




ROW_TEXT_W = DIALOG_W - 2 * SIDE_PAD - BADGE_PX - 14
FOOTER_TEXT_W = 300


def _load_mark(widget, size: int) -> QPixmap | None:
    """The plugin icon at its real size on this screen."""





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


def _glyph(widget, kind: str, color: str) -> QPixmap:
    """One line icon, drawn to the device resolution so it stays crisp."""
    ratio = widget.devicePixelRatioF() or 1.0
    side = int(GLYPH_PX * ratio)
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(ratio)




    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    n = GLYPH_PX

    if kind == "place":

        painter.drawEllipse(QRectF(2.2, 2.2, n - 4.4, n - 4.4))
        painter.drawLine(int(2.2), int(n / 2), int(n - 2.2), int(n / 2))
        painter.drawEllipse(QRectF(n / 2 - 3.4, 2.2, 6.8, n - 4.4))
    elif kind == "clock":
        painter.drawEllipse(QRectF(2.2, 2.2, n - 4.4, n - 4.4))
        painter.drawLine(int(n / 2), int(n / 2), int(n / 2), int(n / 2 - 4))
        painter.drawLine(int(n / 2), int(n / 2), int(n / 2 + 3), int(n / 2 + 2))
    else:

        painter.drawLine(int(2.5), int(n / 2 - 3.5), int(n - 2.5), int(n / 2 - 3.5))
        painter.drawLine(int(2.5), int(n / 2 + 3.5), int(n - 2.5), int(n / 2 + 3.5))
        painter.setBrush(QColor(color))
        painter.drawEllipse(QRectF(n - 8.6, n / 2 - 6.1, 5.2, 5.2))
        painter.drawEllipse(QRectF(3.4, n / 2 + 0.9, 5.2, 5.2))
    painter.end()
    return pixmap


class PrivacyNoticeDialog(QDialog):
    """Tell the user what leaves the machine before the first message does."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("privacyNoticeDialog")
        self.setWindowTitle(tr("Before you start"))
        self.setModal(True)









        available = available_screen_rect(self)
        cap_w = (available.width() - 48) if available is not None else scale_px_length(DIALOG_W)
        cap_h = (available.height() - 48) if available is not None else 0
        self._width = max(320, min(scale_px_length(DIALOG_W), cap_w))
        self._text_width = self._width - 2 * SIDE_PAD - BADGE_PX - 14
        self.setFixedWidth(self._width)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._build_header())




        rows = QWidget(self)
        body = QVBoxLayout(rows)
        body.setContentsMargins(24, 4, 24, 16)
        body.setSpacing(14)
        for kind, lead, rest in self._rows():
            body.addWidget(self._build_row(kind, lead, rest))
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
        """The mark at a size you can see, and the one line that names this."""





        header = QHBoxLayout()
        header.setContentsMargins(24, 22, 24, 6)
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
        heading.setStyleSheet(f"font-size: 17px; font-weight: 600; color: {S.INK};")
        header.addWidget(heading, 1, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _rows(self):
        """Three lines: the transfer, the retention, the statistics."""










        return get_privacy_notice_rows(self._shipped_rows())

    def _shipped_rows(self):
        return (
            (
                "place",







                tr("Runs in Europe, stored in France."),
                tr("Your message and your layer names, never your files. No model "
                   "is trained on them."),
            ),
            (
                "clock",
                tr("Your history stays on your computer."),















                tr("The message you send is the part that reaches us: we keep it "
                   "90 days to make AI Agent better. On {pro}, no copy at all."),
            ),
            (
                "sliders",
                tr("Usage stats are on."),
                tr("They help us fix bugs. You can switch them off in Settings "
                   "whenever you want."),
            ),
        )

    def _build_row(self, kind: str, lead: str, rest: str) -> QFrame:
        row = QFrame(self)
        row.setObjectName("privacyNoticeRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(14)

        badge = QLabel(row)
        badge.setFixedSize(BADGE_PX, BADGE_PX)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {S.T['accent_tint']};"
            f"border-radius: {BADGE_PX // 2}px;"
        )
        badge.setPixmap(_glyph(self, kind, S.accent_ink()))
        line.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)



        text = QLabel(row)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        pro_link = (
            f'<a style="color:{S.accent_ink()};" href="{get_pricing_url()}">'
            f'{tr("Pro")}</a>'
        )



        safe_lead = html.escape(lead)
        safe_rest = html.escape(rest).replace("{pro}", pro_link)
        text.setText(
            f'<span style="font-weight:600;color:{S.INK};">{safe_lead}</span> '
            f'<span style="color:{S.INK_2};">{safe_rest}</span>'
        )
        text.setStyleSheet("font-size: 13px;")
        text.setFixedWidth(self._text_width)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        text.setOpenExternalLinks(False)
        text.linkActivated.connect(lambda url: open_external_url(url, parent=self))
        line.addWidget(text, 1)
        return row

    def _fill_footer(self, footer: QHBoxLayout) -> None:
        """The acceptance line and the single button, on one row."""


        terms_link = f'<a href="{get_terms_url()}">{tr("Terms")}</a>'
        privacy_link = f'<a href="{get_privacy_url()}">{tr("Privacy Policy")}</a>'




        body = tr("Continuing accepts the {terms} and the {privacy}.")
        for token, value in (("{terms}", terms_link), ("{privacy}", privacy_link)):
            body = body.replace(token, value)
        accept_line = QLabel(body, self)
        accept_line.setObjectName("privacyNoticeAcceptLine")
        accept_line.setWordWrap(True)
        accept_line.setTextFormat(Qt.TextFormat.RichText)
        accept_line.setStyleSheet(f"font-size: 11px; color: {S.INK_3};")
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
