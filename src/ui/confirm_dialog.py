# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A product confirmation: a glyph, a short question, a few scannable lines."""

















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

DIALOG_W = 420
SIDE_PAD = 24
TILE_PX = 40
TILE_GLYPH_PX = 20
BADGE_PX = 30
BADGE_GLYPH_PX = 16
ROW_SPACING = 12


def _tile(parent, glyph: str, accent: str, size: int, glyph_size: int, alpha: float) -> QLabel:
    """A round tile: ``glyph`` in ``accent`` on a faint wash of the same colour."""
    color = QColor(accent)
    tile = QLabel(parent)
    tile.setFixedSize(size, size)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tile.setStyleSheet(
        f"QLabel {{ background: rgba({color.red()}, {color.green()}, {color.blue()}, {alpha});"
        f" border: none; border-radius: {size // 2}px; }}")
    tile.setPixmap(pixmap_for(parent, glyph, glyph_size, color))
    return tile


class ConfirmDialog(QDialog):
    """Ask one question; ``accept`` on the action, ``reject`` on Cancel or Escape."""

    def __init__(self, parent=None, *, title: str, confirm_text: str,
                 points=(), note: str = "", cancel_text: str = "",
                 glyph: str = "", accent: str = "", window_title: str = "",
                 object_name: str = "confirmDialog", confirm_is_default: bool = False):
        super().__init__(parent)
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
        body.setContentsMargins(SIDE_PAD, 6, SIDE_PAD, 18)
        body.setSpacing(10)
        for row_glyph, text in points:
            body.addWidget(self._build_row(row_glyph, text))
        if note:
            body.addSpacing(2)
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
                         scale_px_length(TILE_PX), scale_px_length(TILE_GLYPH_PX), 0.16)
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

    def _build_row(self, glyph: str, text: str) -> QFrame:
        row = QFrame(self)
        row.setObjectName("confirmDialogRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(ROW_SPACING)
        badge = _tile(row, glyph, S.INK_2, scale_px_length(BADGE_PX),
                      scale_px_length(BADGE_GLYPH_PX), 0.0)
        badge.setStyleSheet(
            f"QLabel {{ background: {S.FIELD}; border: none;"
            f" border-radius: {scale_px_length(BADGE_PX) // 2}px; }}")
        line.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        label = QLabel(text, row)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setFixedWidth(self._text_width)
        label.setStyleSheet(scale_qss_font_px(
            f"font-size: 13px; color: {S.INK}; background: transparent;"))
        line.addWidget(label, 1, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _build_note(self, note: str) -> QLabel:
        label = QLabel(note, self)
        label.setObjectName("confirmDialogNote")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)


        indent = scale_px_length(BADGE_PX) + ROW_SPACING
        label.setStyleSheet(scale_qss_font_px(
            f"font-size: 12px; color: {S.INK_3}; background: transparent;"
            f" margin-left: {indent}px;"))
        label.setFixedWidth(self._text_width + indent)
        return label


def ask_confirm(parent=None, **options) -> bool:
    """Show a ``ConfirmDialog`` modally; True only when the action was pressed."""
    dialog = ConfirmDialog(parent, **options)
    try:
        return exec_dialog(dialog) == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
