# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

from contextlib import contextmanager

from qgis.core import QgsCredentials


class QuietCredentials(QgsCredentials):


    def request(self, realm, username, password, message=""):
        return False, username, password

    def requestMasterPassword(self, password, stored=False):
        return False, password


@contextmanager
def no_login_prompt():

    previous = QgsCredentials.instance()
    quiet = QuietCredentials()
    quiet.setInstance(quiet)
    try:
        yield
    finally:
        quiet.setInstance(previous)
