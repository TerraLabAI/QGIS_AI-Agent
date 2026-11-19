# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import sys

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QWidget

from . import tokens
from .font_scale import scale_px_length, scale_qss_font_px
from .settings_pages import Page, SettingGroup, SettingRow
from .shared import CHECKPOINTS_KEYS, STEP_BACK_KEYS, STEP_FORWARD_KEYS, toggle_panel_key_text, tr
from .style import DARK

_T = tokens.DARK if DARK else tokens.LIGHT
_OR_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: 11px; color: {_T['text_tertiary']}; background: transparent; padding: 0 4px; }}")



_MAC = sys.platform == "darwin"
_MAC_NAMES = {"Ctrl": "\u2318", "Meta": "\u2303", "Alt": "\u2325", "Shift": "\u21e7",
              "Backspace": "\u232b", "Del": "\u2326", "Return": "\u21b5", "Enter": "\u21b5",
              "Left": "\u2190", "Right": "\u2192", "Up": "\u2191", "Down": "\u2193"}
_PC_NAMES = {"Meta": "Win", "Del": "Delete", "Return": "Enter", "Left": "\u2190", "Right": "\u2192",
             "Up": "\u2191", "Down": "\u2193"}


def key_cap_labels(portable: str, mac: bool = _MAC) -> list:

    parts = portable.split("+")
    if portable.endswith("+"):
        parts = [p for p in parts[:-2] if p] + ["+"]
    names = _MAC_NAMES if mac else _PC_NAMES
    return [names.get(p, p) for p in parts if p]


def _cap_qss(label: str) -> str:

    size = 13 if len(label) == 1 and not label.isalnum() else 11
    return scale_qss_font_px(
        f"QLabel {{ color: {_T['text_primary']}; background: {_T['surface_raised']};"
        f" border: 1px solid {_T['border_default']}; border-bottom: 2px solid {_T['border_strong']};"
        f" border-radius: {tokens.RADIUS_CHIP}px; padding: 0 6px; font-size: {size}px; font-weight: 600; }}")


def key_caps(parent: QWidget, combos: tuple) -> QWidget:

    box = QWidget(parent)
    line = QHBoxLayout(box)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(4)
    height = scale_px_length(22)
    spoken = []
    for index, combo in enumerate(combos):
        if index:
            joiner = QLabel(tr("or"), box)
            joiner.setStyleSheet(_OR_QSS)
            line.addWidget(joiner)
        caps = key_cap_labels(combo)
        for part in caps:
            cap = QLabel(part, box)
            cap.setStyleSheet(_cap_qss(part))
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setFixedHeight(height)
            cap.setMinimumWidth(height)
            line.addWidget(cap)
        spoken.append(" ".join(caps))
    box.setAccessibleName(f" {tr('or')} ".join(spoken))
    return box


def shortcut_sections() -> tuple:




    panel_key = QKeySequence.fromString(toggle_panel_key_text(), QKeySequence.SequenceFormat.NativeText)
    enter_key = "Return"
    esc_key = "Esc"
    composer = (
        (tr("Send the message"), "", (enter_key,)),
        (tr("New line"), "", ("Shift+Return",)),
        (tr("Add a layer from the project"), tr("Or drag it from the Layers panel"), ("@",)),
        (tr("Recall your last message"), "", ("Up",)),
        (tr("Close the open list"), "", (esc_key,)),
    )
    panel = (
        (tr("Open or close AI Agent"), "", (panel_key.toString(QKeySequence.SequenceFormat.PortableText),)),
    )


    history = (
        (tr("Undo the last request"), tr("In this chat"), (STEP_BACK_KEYS,)),
        (tr("Redo it"), "", (STEP_FORWARD_KEYS,)),
        (tr("Open earlier project versions"), "", (CHECKPOINTS_KEYS,)),
    )


    during = (

        (tr("Stop the run"), tr("Press it twice"), (esc_key,)),
        (tr("Queue a message"), tr("The AI agent reads it at its next step"), (enter_key,)),
        (tr("Edit the last queued message"), tr("In an empty box"), ("Up",)),
    )
    return ((tr("Composer"), composer), (tr("Panel"), panel),
            (tr("Map history"), history), (tr("During a run"), during))


def build_shortcuts_page(parent=None) -> Page:

    page = Page(tr("Keyboard shortcuts"), tr("Everything the panel does without leaving the keyboard."), parent)
    for heading, rows in shortcut_sections():
        page.add_group_title(heading)
        group = SettingGroup(page)
        for action, note, combos in rows:
            group.add_row(SettingRow(action, note, key_caps(group, combos), group))
        page.add(group)
    return page
