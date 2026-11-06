# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import hashlib
import os
import re


def key_of(path: str) -> str:

    if not path:
        return ""
    normal = os.path.normcase(os.path.abspath(path))
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:16]


def key_of_address(address: str) -> str:

    if not address:
        return ""
    bare = re.sub(r"//[^/@]*@", "//", address)
    return hashlib.sha256(bare.encode("utf-8")).hexdigest()[:16]


def project_key() -> str:

    try:
        from qgis.core import QgsProject
        project = QgsProject.instance()
        return key_of(project.absoluteFilePath()) or key_of_address(project.fileName())
    except Exception:  # noqa: BLE001
        return ""
