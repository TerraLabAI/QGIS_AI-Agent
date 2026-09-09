# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""User-approved diagnostic report for the AI Agent support flow."""












from __future__ import annotations

import os
import platform
import sys
from urllib.parse import quote

from qgis.PyQt.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout

from ..core.log_scrub import scrub_secrets, scrub_user_paths
from ..core.logger import log_warning, recent_logs
from ..core.settings import account_dir
from .external_links import open_email, open_local_path
from .font_scale import scale_px_length
from .shared import exec_dialog, get_support_email, plugin_version, tr
from .style import _BTN_GHOST, _BTN_PRIMARY, BTN_PILL_PX


def _clean(text: str) -> str:
    """A username is not the only thing that must not reach a support inbox."""
    return scrub_secrets(scrub_user_paths(text))


def diagnostic_text(error_message: str = "", run_id: str = "") -> str:
    """Build a copyable report without local usernames or hidden credentials."""
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
        f"OS: {platform.system()} {platform.release()}",
        f"Architecture: {platform.machine()}",
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
    """Which sizes and rates this session was told to use."""






    try:
        from ..core import tuning

        snap = tuning.snapshot()
    except Exception:  # noqa: BLE001 - a report that cannot be built is worse than a vague one
        return "unavailable"
    tuned = {key: value for key, value in snap.items() if key != "version" and value}
    if not tuned:
        return "none in force (the values this build shipped with)"
    parts = [f"v{snap.get('version', 0)}"]
    parts += [f"{section}: {values}" for section, values in sorted(tuned.items())]
    return "; ".join(parts)


class ErrorReportDialog(QDialog):
    """Three steps, in the order somebody in trouble takes them: copy the session, keep a file of it, open the email."""


    def __init__(self, error_message: str = "", run_id: str = "", parent=None, report_provider=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Report a problem"))
        self.setModal(True)
        self.setMinimumWidth(440)
        self._run_id = str(run_id or "")
        self._error = str(error_message or "")


        self._provider = report_provider
        self._export: dict | None = None
        self._saved_path = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        label = QLabel(self._explanation(), self)
        label.setWordWrap(True)
        layout.addWidget(label)
        self._copy = QPushButton(self._copy_label(), self)
        self._copy.setStyleSheet(_BTN_PRIMARY)
        self._copy.setFixedHeight(scale_px_length(BTN_PILL_PX))
        self._copy.clicked.connect(self._on_copy)
        layout.addWidget(self._copy)
        self._save: QPushButton | None = None
        if report_provider is not None:


            self._save = QPushButton(tr("2. Save it as a file"), self)
            self._save.setStyleSheet(_BTN_GHOST)
            self._save.setFixedHeight(scale_px_length(BTN_PILL_PX))
            self._save.clicked.connect(self._on_save)
            layout.addWidget(self._save)
        email = get_support_email()



        step = 3 if self._save is not None else 2
        self._email = QPushButton(
            tr("{step}. Open an email to {email}").format(step=step, email=email), self)
        self._email.setStyleSheet(_BTN_GHOST)
        self._email.setFixedHeight(scale_px_length(BTN_PILL_PX))
        self._email.clicked.connect(self._on_email)
        layout.addWidget(self._email)

    def _copy_label(self) -> str:
        if self._provider is None:
            return tr("1. Copy diagnostics")
        return tr("1. Copy the whole session")

    def _explanation(self) -> str:
        """What is about to be copied, said before it is copied."""





        if self._provider is None:
            return tr("Copy the diagnostics, then send them to support.")
        return tr(
            "The report holds this whole session: your messages, every tool the agent ran "
            "with its arguments and what came back, the plan it followed and the recent log "
            "lines. Your activation key, your passwords and the contents of your files are "
            "never in it.")



    def report(self) -> dict | None:
        """The structured export, built once. None when there is no session."""
        if self._export is None and self._provider is not None:
            try:
                self._export = self._provider(self._run_id, self._error)
            except Exception as exc:  # noqa: BLE001 - a report is never the thing that fails
                log_warning(f"The session report could not be built: {exc}")
                self._provider = None
        return self._export

    def text(self) -> str:
        """What goes on the clipboard: the session, or the header alone."""
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
        counts = (self.report() or {}).get("counts") or {}
        if counts:
            self._copy.setText(tr("Copied: {runs} runs, {calls} tool calls").format(
                runs=counts.get("runs", 0), calls=counts.get("tool_calls", 0)))
        else:
            self._copy.setText(tr("Copied"))

    def _on_save(self) -> None:
        if self._save is None:
            return
        if self._saved_path:
            open_local_path(self._saved_path, parent=self)
            return
        export = self.report()
        if export is None:
            self._save.setText(tr("There is no session to save"))
            return
        from ..core.session_export import default_name, write_json

        folder = os.path.join(account_dir(), "reports")
        try:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, default_name(export))
            write_json(path, export)
        except OSError as exc:
            log_warning(f"The session report could not be written: {exc}")
            self._save.setText(tr("The file could not be written"))
            return
        self._saved_path = path


        self._save.setText(tr("Saved. Open {name}").format(name=os.path.basename(path)))

    def _on_email(self) -> None:
        email = get_support_email()
        mailto = f"mailto:{email}?subject={quote('AI Agent problem report')}"


        open_email(mailto, email, parent=self)


def provider_near(widget):
    """The panel's report builder, found from any widget inside the panel."""






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
