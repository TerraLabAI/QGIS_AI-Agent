# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

_PROJ_TAIL = (" +no_defs", " +type=crs")


def crs_ref(crs) -> str:

    try:
        if crs is None or not crs.isValid():
            return ""
        authid = str(crs.authid() or "")
        if authid:
            return authid
        proj = str(crs.toProj() or "").strip()
        for tail in _PROJ_TAIL:
            proj = proj.replace(tail, "")
        if proj:
            return f"PROJ:{proj}"
        return str(crs.description() or "") or "custom CRS"
    except (AttributeError, RuntimeError, TypeError):
        return ""
