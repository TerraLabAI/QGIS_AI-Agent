# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""No Enter Credentials dialog while the agent opens something."""







from __future__ import annotations

from contextlib import contextmanager

from qgis.core import QgsCredentials


class QuietCredentials(QgsCredentials):
    """Refuse every credential request instead of opening the Enter Credentials dialog."""

    def request(self, realm, username, password, message=""):
        return False, username, password

    def requestMasterPassword(self, password, stored=False):
        return False, password


@contextmanager
def no_login_prompt():
    """Main thread only, around the call that may ask; the user's own handler comes back after."""
    previous = QgsCredentials.instance()
    quiet = QuietCredentials()
    quiet.setInstance(quiet)
    try:
        yield
    finally:
        quiet.setInstance(previous)
