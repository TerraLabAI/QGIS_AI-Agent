# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import time

from qgis.PyQt.QtCore import QCoreApplication

from ..api.account import Account
from . import telemetry
from . import telemetry_events as ev
from .controller_actions import _project_path
from .logger import log, log_warning
from .protocol import CANCEL_SIGNED_OUT, RunStatus
from .threads import ThreadStore


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


class _ControllerAccount:


    def _on_sign_in(self) -> None:
        if self._account.state == Account.PAIRING:
            self._account.reopen_pairing_page()
            return
        if self._account.state == Account.LOCKED and self._account.unlock():



            log("Sign-in read from the QGIS auth database after the master password")
            self._panel_call("set_connection_state", "connecting", "")
            self._session.connect_to_server()
            self._account.refresh_activation_async(force=True)
            return
        self._pairing_started = time.monotonic()
        telemetry.track(ev.PAIRING_STARTED)
        self._account.start_pairing()

    def _on_paired(self, prefix: str) -> None:
        log(f"Signed in with key {prefix}...")
        sibling, self._account.signed_in_from = self._account.signed_in_from, None
        if sibling is None:



            self._raise_qgis()
        telemetry.track(ev.PLUGIN_ACTIVATED, {"activation_method": "sibling" if sibling else "pairing"})
        telemetry.flush()
        self._session.forget_session()

        self._bind_account(keep_view=True)
        self._panel_call("set_connection_state", "connecting", "")
        self._session.connect_to_server()
        if sibling is None:
            self.notice.emit("info", tr("Signed in to TerraLab."))
        elif sibling[0]:
            self.notice.emit("info", tr("Signed in as {} (from {}).").format(*sibling))
        else:
            self.notice.emit("info", tr("Signed in (from {}).").format(sibling[1]))

    def _on_signed_out(self) -> None:

        self._bind_account(keep_view=False)
        self._session.disconnect_from_server()
        self._session.forget_session()
        self._panel_call("set_connection_state", "signed_out", "")

    def _on_session_expired(self, message: str) -> None:


        self._stop_run_for_account()
        self._session.disconnect_from_server()
        self._session.forget_session()
        self._panel_call("set_connection_state", "signed_out", message)
        self.notice.emit("warning", message)

    def _stop_run_for_account(self) -> None:

        run = self._runs.stop("")
        if run is None:
            return
        try:
            self._session.send_cancel(run["run_id"], CANCEL_SIGNED_OUT)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Run {run['run_id'][:8]}: no cancel sent when the account left: {exc}")
        finally:
            self._finish_run(run["run_id"], RunStatus.CANCELLED, tr("Stopped: signed out."), {}, None)

    def _bind_account(self, keep_view: bool) -> None:







        from .settings import account_tag
        from .threads import SIGNED_OUT

        moved = account_tag() != self._store.account
        if moved or not keep_view:
            self._stop_run_for_account()
            self._thread_id = None
            self._last_run = None
            self._last_verification = None
            self._panel_call("clear_thread")
            self._panel_call("set_current_thread", "")
            self._panel_call("set_run_changes", 0, False)
            self._send_history()
        if not keep_view or (moved and self._store.account != SIGNED_OUT):

            self._panel_call("clear_draft")
        if moved:
            self._store.close()
            self._store = ThreadStore(on_index_ready=self._on_thread_index_ready)
            try:


                self._executor.bind_account()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Tool history not moved to the signed-in account: {exc}")
        self._panel_call("set_threads", self._store.list_threads(), _project_path())

    def _bind_unlocked_account(self, _session=None) -> None:


        from .settings import account_tag, reset_account_tag_cache
        from .threads import SIGNED_OUT

        if self._store.account != SIGNED_OUT:
            return
        reset_account_tag_cache()
        if account_tag() != SIGNED_OUT:
            self._bind_account(keep_view=True)

    def _on_pairing_failed(self, message: str, code: str) -> None:
        telemetry.track(ev.PAIRING_FAILED, {"error_code": code, "duration_ms": self._since_pairing_ms()})





        self._panel_call("set_connection_state", "signed_out", "")
        self._panel_call("set_connection_state", "error", message)

    def _raise_qgis(self) -> None:

        try:
            from .window_focus import bring_qgis_window_to_front

            window = self._iface.mainWindow() if self._iface is not None else None
            dock = getattr(self._panel, "window", None)
            bring_qgis_window_to_front(window, dock() if callable(dock) else None)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"QGIS could not be raised after the sign-in: {exc}")

    def _since_pairing_ms(self) -> int | None:
        started = self._pairing_started
        self._pairing_started = None
        return int((time.monotonic() - started) * 1000) if started else None

    def _on_account_state(self, state: str, message: str) -> None:
        if state == Account.LOCKED:



            self._panel_call("set_connection_state", "error", message)
