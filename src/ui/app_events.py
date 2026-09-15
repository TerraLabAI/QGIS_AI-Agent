# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One application event filter for the two things no widget of ours can see."""
















from __future__ import annotations

import sys

from qgis.PyQt.QtCore import QEvent, QObject, Qt

_SHORTCUT_OVERRIDE = QEvent.Type.ShortcutOverride
_PALETTE_CHANGE = QEvent.Type.ApplicationPaletteChange


def _as_int(value) -> int:
    """An enum or flag as an int on PyQt5 and PyQt6."""
    return int(getattr(value, "value", value))


_CTRL = _as_int(Qt.KeyboardModifier.ControlModifier)
_ALT = _as_int(Qt.KeyboardModifier.AltModifier)
_SHIFT = _as_int(Qt.KeyboardModifier.ShiftModifier)
_META = _as_int(Qt.KeyboardModifier.MetaModifier)
_MODS = _CTRL | _ALT | _SHIFT | _META
_KEY_MASK = 0x01FFFFFF


def _first_combination(sequence):
    """``(key, modifiers)`` of a sequence's first chord, or None when empty."""
    try:
        if sequence.isEmpty():
            return None
        combo = sequence[0]
    except (AttributeError, IndexError, TypeError):
        return None
    combined = getattr(combo, "toCombined", None)
    value = combined() if combined is not None else _as_int(combo)
    return value & _KEY_MASK, value & _MODS


def is_altgr_text(event, sequences) -> bool:
    """True when a Ctrl+Alt key event types a character and matches one of ``sequences``."""
    modifiers = _as_int(event.modifiers()) & _MODS
    if modifiers & (_CTRL | _ALT) != (_CTRL | _ALT):
        return False
    text = event.text()
    if not text or not text[0].isprintable():
        return False
    key = event.key()


    native = event.nativeVirtualKey()
    for sequence in sequences:
        chord = _first_combination(sequence)
        if chord is None or chord[1] != modifiers:
            continue
        if chord[0] == key or (0x30 <= native <= 0x5A and chord[0] == native):
            return True
    return False




PANEL_KEYS = ("Ctrl+Alt+Z", "Ctrl+Alt+Shift+Z", "Ctrl+Alt+H")


def layouts_type_text(sequences) -> bool:
    """True when a keyboard layout installed on this Windows machine types a character for one of the Ctrl+Alt ``sequences``."""










    if sys.platform != "win32":
        return False
    chords = []
    for sequence in sequences:
        chord = _first_combination(sequence)
        if chord is None or chord[1] & (_CTRL | _ALT) != (_CTRL | _ALT):
            continue
        if not 0x30 <= chord[0] <= 0x5A:
            return True
        chords.append((chord[0], bool(chord[1] & _SHIFT)))
    if not chords:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(wintypes.HKL)]
        user32.GetKeyboardLayoutList.restype = ctypes.c_int
        user32.MapVirtualKeyExW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HKL]
        user32.MapVirtualKeyExW.restype = wintypes.UINT
        user32.ToUnicodeEx.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte),
                                       wintypes.LPWSTR, ctypes.c_int, wintypes.UINT, wintypes.HKL]
        user32.ToUnicodeEx.restype = ctypes.c_int
        count = user32.GetKeyboardLayoutList(0, None)
        layouts = (wintypes.HKL * max(count, 1))()
        count = user32.GetKeyboardLayoutList(count, layouts)
        if count <= 0:
            return True
        for layout in layouts[:count]:
            for virtual_key, shift in chords:
                state = (ctypes.c_ubyte * 256)()

                for held in (0x11, 0x12, 0xA2, 0xA5) + ((0x10,) if shift else ()):
                    state[held] = 0x80
                out = ctypes.create_unicode_buffer(8)
                scan = user32.MapVirtualKeyExW(virtual_key, 0, layout)
                got = user32.ToUnicodeEx(virtual_key, scan, state, out, 8, 0x4, layout)
                if got < 0 or (got > 0 and out.value[:1].isprintable() and out.value[:1] != ""):
                    return True
        return False
    except Exception:  # noqa: BLE001 - unknown means keep the guard
        return True


class AppEvents(QObject):
    """Installed on the application by the plugin and removed at unload."""






    def __init__(self, shortcuts, altgr: bool | None = None):
        super().__init__(None)
        self._shortcuts = shortcuts
        self._altgr = sys.platform == "win32" if altgr is None else altgr
        self.on_palette = None

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        kind = event.type()
        if kind == _SHORTCUT_OVERRIDE:
            if self._altgr and self._claims(event):
                event.accept()
                return True
        elif kind == _PALETTE_CHANGE and self.on_palette is not None:

            from qgis.PyQt.QtCore import QCoreApplication

            if watched is QCoreApplication.instance():
                try:
                    self.on_palette()
                except Exception:  # nosec B110 - a notice never breaks the event loop
                    pass
        return False

    def _claims(self, event) -> bool:
        try:
            return is_altgr_text(event, self._shortcuts() or ())
        except (AttributeError, RuntimeError, TypeError):
            return False
