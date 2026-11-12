# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import sys
import tempfile
import time

from qgis.PyQt.QtCore import QCoreApplication, QProcess, QTimer

from .dictation import Recorder

APP_NAME = "AI Agent for QGIS"
BUNDLE_ID = "ai.terra-lab.qgis-ai-agent.dictation"
_POLL_MS = 40

_START_SECONDS = 10.0
_FLUSH_SECONDS = 1.5
_FAILURES = {"denied": "mic_denied", "unanswered": "mic_unanswered", "error": "mic_error"}

_LAUNCHER = """#!/bin/sh
here="$(cd "$(dirname "$0")" && pwd)"
export PYTHONHOME="{home}"
exec "$here/pyhelper" -s -B "$here/../Resources/helper.py" "$@"
"""


def _tr(text: str) -> str:
    return QCoreApplication.translate("Dictation", text)


def _qgis_python() -> tuple[str, str] | None:

    contents = os.path.dirname(os.path.dirname(os.path.abspath(sys.prefix.rstrip("/"))))
    candidates = [os.path.join(os.path.dirname(os.path.abspath(sys.prefix)), "MacOS"),
                  os.path.join(contents, "MacOS")]
    name = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for folder in candidates:
        for binary in (name, "python3", "python"):
            path = os.path.join(folder, binary)
            if os.path.isfile(path) and os.path.isdir(os.path.join(os.path.dirname(folder), "Frameworks")):
                return path, os.path.dirname(folder)
    return None


def helper_app() -> str | None:

    found = _qgis_python()
    if found is None:
        return None
    python, contents = found
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "workers", "mac_dictation_helper.py")
    icon = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "icons", "dictation-helper.icns")
    usage = _tr("The AI Agent plugin in QGIS listens only while you dictate a message, to turn your"
                " voice into text.")
    try:
        stamp = json.dumps([python, os.path.getmtime(python), os.path.getsize(python),
                            os.path.getmtime(script), os.path.getsize(script), sys.prefix, usage])
    except OSError:
        return None
    install = hashlib.sha256(contents.encode("utf-8")).hexdigest()[:10]
    root = os.path.join(os.path.expanduser("~/Library/Application Support/TerraLab/Dictation"), install)
    app = os.path.join(root, APP_NAME + ".app")
    stamp_file = os.path.join(root, "built.json")
    try:
        with open(stamp_file, encoding="utf-8") as fh:
            if fh.read() == stamp and os.path.isfile(os.path.join(app, "Contents", "MacOS", "pyhelper")):
                return app
    except OSError:
        pass
    try:
        shutil.rmtree(app, ignore_errors=True)
        macos = os.path.join(app, "Contents", "MacOS")
        resources = os.path.join(app, "Contents", "Resources")
        os.makedirs(macos)
        os.makedirs(resources)
        shutil.copy2(python, os.path.join(macos, "pyhelper"))
        frameworks = os.path.join(app, "Contents", "Frameworks")
        os.symlink(os.path.join(contents, "Frameworks"), frameworks)
        shutil.copyfile(script, os.path.join(resources, "helper.py"))
        info = {
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleExecutable": "launcher",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1",
            "LSUIElement": True,
            "NSMicrophoneUsageDescription": usage,
            "NSHumanReadableCopyright": "TerraLab, GPL-2.0-or-later. Part of the AI Agent plugin for QGIS.",
        }
        if os.path.isfile(icon):
            shutil.copyfile(icon, os.path.join(resources, "icon.icns"))
            info["CFBundleIconFile"] = "icon"
        with open(os.path.join(app, "Contents", "Info.plist"), "wb") as fh:
            plistlib.dump(info, fh)
        launcher = os.path.join(macos, "launcher")
        with open(launcher, "w", encoding="utf-8") as fh:
            fh.write(_LAUNCHER.format(home=sys.prefix.replace('"', '\\"')))
        os.chmod(launcher, 0o700)
        with open(stamp_file, "w", encoding="utf-8") as fh:
            fh.write(stamp)
    except OSError:
        return None
    return app


class HelperRecorder(Recorder):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._folder = ""
        self._read_at = 0
        self._status = ""
        self._launched_at = 0.0
        self._stop_at = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self, max_seconds: int) -> None:
        app = helper_app()
        if app is None:
            self.failed.emit("unsupported")
            return
        self._folder = tempfile.mkdtemp(prefix="terralab-dictation-")

        started = QProcess.startDetached(
            "/usr/bin/open", ["-n", "-g", app, "--args", self._folder, str(int(max_seconds)), str(os.getpid())])
        if not (started[0] if isinstance(started, tuple) else started):
            self._clean()
            self.failed.emit("mic_error")
            return
        self._launched_at = time.monotonic()
        self._timer.start()

    def _poll(self) -> None:
        try:
            with open(os.path.join(self._folder, "status"), encoding="utf-8") as fh:
                status = fh.read().strip()
        except OSError:
            status = ""
        if status != self._status:
            self._status = status
            if status == "asking":
                self.asking.emit()
            elif status == "recording":
                self.started.emit()
            elif status in _FAILURES:
                self._clean()
                self.failed.emit(_FAILURES[status])
                return
        if status in ("recording", "done"):
            self._drain()
        if status == "done" or (self._stop_at and time.monotonic() - self._stop_at > _FLUSH_SECONDS):
            self._clean()
            self.finished.emit()
        elif not status and time.monotonic() - self._launched_at > _START_SECONDS:
            self._clean()
            self.failed.emit("mic_error")

    def _drain(self) -> None:
        try:
            with open(os.path.join(self._folder, "audio.pcm"), "rb") as fh:
                fh.seek(self._read_at)
                data = fh.read()
        except OSError:
            return
        data = data[:len(data) // 2 * 2]
        if data:
            self._read_at += len(data)
            self.chunk.emit(data)

    def stop(self) -> None:
        if not self._folder:
            self.finished.emit()
            return
        self._ask_stop()
        self._stop_at = time.monotonic()

    def _ask_stop(self) -> None:
        try:
            with open(os.path.join(self._folder, "stop"), "w", encoding="utf-8"):
                pass
        except OSError:
            pass

    def abort(self) -> None:
        self._clean()

    def _clean(self) -> None:

        self._timer.stop()
        folder, self._folder = self._folder, ""
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
