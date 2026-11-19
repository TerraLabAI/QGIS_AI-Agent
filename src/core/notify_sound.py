# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import os
import sys
import time

from .logger import log_warning


SOUNDS = {
    "chime": "chime.wav",
    "marimba": "marimba.wav",
    "bell": "bell.wav",
    "train": "train.wav",
}

_SOUND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sounds")

_MIN_GAP_S = 1.5


_effects: dict = {}
_pending: set = set()
_last_played = 0.0
_media = None


def sound_path(key: str) -> str:
    return os.path.join(_SOUND_DIR, SOUNDS.get(key, SOUNDS["chime"]))



VOLUME = 60


def play(key: str, force: bool = False) -> str:





    global _last_played
    now = time.monotonic()
    if not force and now - _last_played < _MIN_GAP_S:
        return "none"
    _last_played = now
    path = sound_path(key)
    try:
        effect = _effect(path)
        if effect is not None:

            effect.setVolume((VOLUME / 100.0) ** 2)
            effect.stop()
            if effect.isLoaded():
                effect.play()
            else:

                _pending.add(path)
            return "qt"
        if sys.platform.startswith("win"):
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return "winsound"
        from qgis.PyQt.QtWidgets import QApplication

        QApplication.beep()
        return "beep"
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Notification sound not played: {exc}")
        return "none"


def _effect(path: str):
    global _media
    if _media is None:
        try:
            from qgis.PyQt import QtMultimedia
            _media = QtMultimedia if hasattr(QtMultimedia, "QSoundEffect") else False
        except ImportError:
            _media = False
    if not _media:
        return None
    effect = _effects.get(path)
    if effect is None:
        from qgis.PyQt.QtCore import QUrl

        effect = _media.QSoundEffect()
        effect.statusChanged.connect(_play_when_loaded)
        effect.setSource(QUrl.fromLocalFile(path))
        _effects[path] = effect
    return effect


def _play_when_loaded() -> None:
    for path in list(_pending):
        effect = _effects.get(path)
        if effect is not None and effect.isLoaded():
            _pending.discard(path)
            effect.play()


def release() -> None:

    _pending.clear()
    for effect in _effects.values():
        try:
            effect.stop()
            effect.deleteLater()
        except RuntimeError:
            pass
    _effects.clear()
