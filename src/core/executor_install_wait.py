# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QTimer

from .logger import log
from .protocol import Decision


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


def _loaded(keys) -> bool:
    try:
        import qgis.utils
        return any(key in qgis.utils.plugins for key in keys)
    except Exception:  # noqa: BLE001
        return False


class _ExecutorInstallWait:
    def _wait_for_install(self, call: dict, result: dict, started: float) -> bool:


        wait = result.pop("wait_for_plugin", None)
        keys = [str(k) for k in (wait or {}).get("keys") or [] if str(k)] if isinstance(wait, dict) else []
        if not keys or _loaded(keys):
            return False
        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        name = str(wait.get("name") or keys[0])
        call["install_wait"] = {"keys": keys, "name": name, "first": result,
                                "then": wait.get("then") if isinstance(wait.get("then"), dict) else {},
                                "started": started}
        call["sentence"] = tr("Waiting for {name} to install").format(name=name)
        self._questions[tool_call_id] = call
        self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
        self.install_wait_needed.emit(tool_call_id, run_id, name)
        timer = QTimer(self)
        timer.setInterval(1000)

        def _tick():
            if tool_call_id not in self._questions:
                timer.stop()
                timer.deleteLater()
                return
            if _loaded(keys):
                timer.stop()
                timer.deleteLater()
                self.question_answered_elsewhere.emit(tool_call_id, tr("{name} is installed").format(name=name))

        timer.timeout.connect(_tick)
        timer.start()
        log(f"INSTALL_WAIT {tool_call_id}: {name}")
        return True

    def on_question_answered(self, tool_call_id: str, answer: str, attachments=None) -> None:
        call = self._questions.get(tool_call_id)
        wait = call.get("install_wait") if isinstance(call, dict) else None
        if not wait:
            super().on_question_answered(tool_call_id, answer, attachments)
            return
        self._questions.pop(tool_call_id, None)
        call.pop("install_wait", None)
        if _loaded(wait["keys"]):
            out = {"done": "plugin_installed", "plugin": wait["name"], "installed": True, **wait["then"]}
            shown = answer or tr("{name} is installed").format(name=wait["name"])
        else:
            out = dict(wait["first"])
            answer = (answer or "").strip()
            if answer:
                out["user_said"] = answer
            else:
                out["skipped_wait"] = f"The user skipped waiting; {wait['name']} is not loaded."
            shown = answer
        self.question_resolved.emit(tool_call_id, shown)
        self._deliver(call, out, wait["started"], held=True)
