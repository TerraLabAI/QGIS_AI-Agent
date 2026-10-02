# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















from __future__ import annotations

from contextlib import contextmanager

from qgis.core import QgsCredentials
from qgis.PyQt import sip


_HOLD_TIMEOUT_S = 10


class QuietCredentials(QgsCredentials):



    refuses_logins = True

    def __init__(self):
        super().__init__()
        self.previous = None
        self.holders = 0

    def request(self, realm, username, password, message=""):
        return False, username, password

    def requestMasterPassword(self, password, stored=False):
        return False, password


_QUIET: list = []


def _quiet() -> QuietCredentials:

    if not _QUIET:
        quiet = QuietCredentials()
        sip.transferto(quiet, None)
        _QUIET.append(quiet)
    return _QUIET[0]


def _installed():

    current = QgsCredentials.instance()
    return current if getattr(current, "refuses_logins", False) else None


def _hold() -> None:

    quiet = _installed()
    if quiet is None:
        quiet = _quiet()
        quiet.previous = QgsCredentials.instance()
        quiet.holders = 0
        quiet.setInstance(quiet)
    quiet.holders += 1


def _release() -> None:

    quiet = _installed()
    if quiet is None:
        return
    quiet.holders -= 1
    if quiet.holders <= 0 and quiet.previous is not None:
        quiet.setInstance(quiet.previous)
        quiet.previous = None


def _hold_if_awaited() -> bool:

    from .background import still_awaited

    if not still_awaited():
        return False
    _hold()
    return True


def hold_from_worker() -> bool:





    from .background import run_on_main_thread

    return bool(run_on_main_thread(_hold_if_awaited, timeout=_HOLD_TIMEOUT_S))


def release_from_worker() -> None:

    from .background import main_thread_invoker

    main_thread_invoker().invoke(_release)


@contextmanager
def no_login_prompt():

    _hold()
    try:
        yield
    finally:
        _release()
