# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from . import style as S
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .settings_pages import GHOST_BTN_QSS, PRIMARY_BTN_QSS
from .shared import available_screen_rect, exec_dialog, tr
from .widgets import break_anywhere

DIALOG_W = 420
SIDE_PAD = 24
TILE_PX = 28
TILE_GLYPH_PX = 22
BADGE_PX = 28
BADGE_GLYPH_PX = 17
ROW_SPACING = 12


def _tile(parent, glyph: str, accent: str, size: int, glyph_size: int) -> QLabel:

    color = QColor(accent)
    tile = QLabel(parent)
    tile.setFixedSize(size, size)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tile.setStyleSheet("QLabel { background: transparent; border: none; }")
    tile.setPixmap(pixmap_for(parent, glyph, glyph_size, color))
    return tile


class ConfirmDialog(QDialog):


    def __init__(self, parent=None, *, title: str, confirm_text: str,
                 points=(), note: str = "", cancel_text: str = "",
                 glyph: str = "", accent: str = "", window_title: str = "",
                 object_name: str = "confirmDialog", confirm_is_default: bool = False):
        super().__init__(parent)
        S.with_platform_font(self)
        self.setObjectName(object_name)
        self.setWindowTitle(window_title or title)
        self.setModal(True)

        available = available_screen_rect(self)
        cap_w = (available.width() - 48) if available is not None else scale_px_length(DIALOG_W)
        self._width = max(300, min(scale_px_length(DIALOG_W), cap_w))


        self._text_width = self._width - 2 * SIDE_PAD - scale_px_length(BADGE_PX) - ROW_SPACING
        self.setFixedWidth(self._width)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._build_header(title, glyph, accent))

        body = QVBoxLayout()
        body.setContentsMargins(SIDE_PAD, 10, SIDE_PAD, 20)
        body.setSpacing(16)
        for point in points:
            body.addWidget(self._build_row(*point))
        if note:
            body.addSpacing(4)
            body.addWidget(self._build_note(note))
        layout.addLayout(body)

        rule = QFrame(self)
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"border: none; background: {S.LINE};")
        layout.addWidget(rule)

        footer = QHBoxLayout()
        footer.setContentsMargins(SIDE_PAD, 13, SIDE_PAD, 15)
        footer.setSpacing(8)
        footer.addStretch(1)
        self.cancel_button = QPushButton(cancel_text or tr("Cancel"), self)
        self.cancel_button.setObjectName(f"{object_name}Cancel")
        self.cancel_button.setStyleSheet(GHOST_BTN_QSS)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.confirm_button = QPushButton(confirm_text, self)
        self.confirm_button.setObjectName(f"{object_name}Confirm")
        self.confirm_button.setStyleSheet(PRIMARY_BTN_QSS)
        self.confirm_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_button.clicked.connect(self.accept)
        footer.addWidget(self.confirm_button)
        layout.addLayout(footer)



        default = self.confirm_button if confirm_is_default else self.cancel_button
        default.setDefault(True)
        default.setFocus()

    def _build_header(self, title: str, glyph: str, accent: str) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setContentsMargins(SIDE_PAD, 22, SIDE_PAD, 12)
        header.setSpacing(ROW_SPACING + 2)
        if glyph:
            tile = _tile(self, glyph, accent or S.accent_ink(),
                         scale_px_length(TILE_PX), scale_px_length(TILE_GLYPH_PX))
            tile.setObjectName("confirmDialogGlyph")
            header.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)
        heading = QLabel(title, self)
        heading.setObjectName("confirmDialogTitle")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        heading.setStyleSheet(scale_qss_font_px(
            f"font-size: 17px; font-weight: 600; color: {S.INK}; background: transparent;"))
        header.addWidget(heading, 1, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _build_row(self, glyph: str, text: str, accent: str = "") -> QFrame:


        row = QFrame(self)
        row.setObjectName("confirmDialogRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(ROW_SPACING)
        badge = _tile(row, glyph, accent or S.INK_2, scale_px_length(BADGE_PX),
                      scale_px_length(BADGE_GLYPH_PX))
        line.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        lead, _, detail = text.partition("\n")
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        styles = ([f"font-size: 13px; font-weight: 600; color: {S.INK};",
                   f"font-size: 13px; color: {S.INK_2};"] if detail
                  else [f"font-size: 13px; color: {S.INK};"])
        for part, style in zip([lead, detail] if detail else [lead], styles):

            label = QLabel(break_anywhere(part), row)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setFixedWidth(self._text_width)
            label.setStyleSheet(scale_qss_font_px(style + " background: transparent;"))


            label.ensurePolished()
            label.setMinimumHeight(label.heightForWidth(self._text_width))
            words.addWidget(label)
        line.addLayout(words, 1)
        return row

    def _build_note(self, note: str) -> QLabel:
        label = QLabel(note, self)
        label.setObjectName("confirmDialogNote")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setIndent(0)


        indent = scale_px_length(BADGE_PX) + ROW_SPACING
        label.setStyleSheet(scale_qss_font_px(
            f"font-size: 12px; color: {S.INK_3}; background: transparent;"
            f" margin-left: {indent}px;"))
        label.setFixedWidth(self._text_width + indent)
        label.ensurePolished()
        label.setMinimumHeight(label.heightForWidth(self._text_width + indent))
        return label


def ask_confirm(parent=None, **options) -> bool:

    dialog = ConfirmDialog(parent, **options)
    try:
        return exec_dialog(dialog) == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
