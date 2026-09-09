# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import sys

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout

from ..core.host_platform import os_info, os_label
from ..core.log_scrub import scrub_secrets, scrub_user_paths
from ..core.logger import log_warning, recent_logs
from .font_scale import scale_px_length
from .shared import exec_dialog, get_support_email, plugin_version, tr
from .style import _BTN_GHOST, _BTN_PRIMARY, BTN_PILL_PX


def _clean(text: str) -> str:

    return scrub_secrets(scrub_user_paths(text))


def diagnostic_text(error_message: str = "", run_id: str = "") -> str:

    lines = ["=== AI Agent error report ===", ""]
    if error_message:
        lines.extend(["--- Error ---", _clean(error_message), ""])
    if run_id:
        lines.extend(["--- Run ---", f"Run ID: {run_id}", ""])
    lines.extend([
        "--- Plugin ---",
        f"Version: {plugin_version() or 'unknown'}",
        "",
        "--- System ---",
        f"OS: {os_label()}",
        f"Architecture: {os_info()[2]}",
        f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    ])
    try:
        from qgis.core import Qgis
        lines.append(f"QGIS: {Qgis.QGIS_VERSION}")
    except Exception:  # noqa: BLE001
        lines.append("QGIS: unknown")
    lines.extend(["", "--- Server policy ---", _policy_line()])
    lines.extend(["", "--- Recent logs ---", recent_logs(), "", "=== End of report ==="])
    return _clean("\n".join(lines))


def _policy_line() -> str:







    try:
        from ..core import tuning

        snap = tuning.snapshot()
    except Exception:  # noqa: BLE001
        return "unavailable"
    tuned = {key: value for key, value in snap.items() if key != "version" and value}
    if not tuned:
        return "none in force (the values this build shipped with)"
    parts = [f"v{snap.get('version', 0)}"]
    parts += [f"{section}: {values}" for section, values in sorted(tuned.items())]
    return "; ".join(parts)


class ErrorReportDialog(QDialog):


    def __init__(self, error_message: str = "", run_id: str = "", parent=None, report_provider=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Report a problem"))
        self.setModal(True)
        self.setMinimumWidth(440)
        self._run_id = str(run_id or "")
        self._error = str(error_message or "")


        self._provider = report_provider
        self._export: dict | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title = QLabel(tr("Tell us what went wrong"), self)
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)
        label = QLabel(self._explanation(), self)
        label.setWordWrap(True)
        layout.addWidget(label)
        if report_provider is not None:
            from .settings_pages import ROW_NOTE_QSS




            detail = QLabel(self._included(), self)
            detail.setWordWrap(True)
            detail.setStyleSheet(ROW_NOTE_QSS)
            layout.addWidget(detail)
        self._copy = QPushButton(self._copy_label(), self)
        self._copy.setStyleSheet(_BTN_PRIMARY)
        self._copy.setFixedHeight(scale_px_length(BTN_PILL_PX))
        self._copy.clicked.connect(self._on_copy)
        layout.addWidget(self._copy)


        to = QLabel(tr("Then paste it into an email to:"), self)
        to.setWordWrap(True)
        layout.addSpacing(4)
        layout.addWidget(to)
        address = QLabel(f"<b>{get_support_email()}</b>", self)
        address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(address)
        self._email = QPushButton(tr("Copy email address"), self)
        self._email.setStyleSheet(_BTN_GHOST)
        self._email.setFixedHeight(scale_px_length(BTN_PILL_PX))
        self._email.clicked.connect(self._on_email)
        layout.addWidget(self._email)

    def _copy_label(self) -> str:
        return tr("Copy report")

    def _explanation(self) -> str:






        return tr("Send us a report and we will look into it and fix it.")

    def _included(self) -> str:

        return tr(
            "This conversation: your messages, each step the AI took and what it found, "
            "and technical details about QGIS and the plugin. Never your passwords, your "
            "sign-in or the contents of your files.")



    def report(self) -> dict | None:

        if self._export is None and self._provider is not None:
            try:
                self._export = self._provider(self._run_id, self._error)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"The session report could not be built: {exc}")
                self._provider = None
        return self._export

    def text(self) -> str:

        export = self.report()
        if export is None:
            return diagnostic_text(self._error, self._run_id)
        from ..core.session_export import render_markdown

        return render_markdown(export)



    def _on_copy(self) -> None:



        try:
            clipboard = QApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("no clipboard")
            clipboard.setText(self.text())
        except (RuntimeError, AttributeError):
            self._copy.setText(tr("Could not copy"))
            return


        self._copy.setText(tr("Report copied"))

    def _on_email(self) -> None:
        try:
            clipboard = QApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("no clipboard")
            clipboard.setText(get_support_email())
        except (RuntimeError, AttributeError):
            self._email.setText(tr("Could not copy"))
            return
        self._email.setText(tr("Email address copied"))


def provider_near(widget):







    node = widget
    for _ in range(12):
        if node is None:
            return None
        provider = getattr(node, "diagnostics_provider", None)
        if callable(provider):
            return provider
        node = node.parent() if hasattr(node, "parent") else None
    return None


def show_error_report(parent=None, error_message: str = "", run_id: str = "",
                      report_provider=None) -> None:
    provider = report_provider or provider_near(parent)
    exec_dialog(ErrorReportDialog(error_message, run_id, parent, provider))
