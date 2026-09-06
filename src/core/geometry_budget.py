# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

import time

from . import limits


def vertex_count(geom) -> int:

    try:
        inner = geom.constGet()
        return int(inner.nCoordinates()) if inner is not None else 0
    except Exception:  # noqa: BLE001
        return 0


class VertexBudget:


    def __init__(self) -> None:
        self.per_geometry = int(limits.current("GEOMETRY_CHECK_MAX_VERTICES"))
        self.total = int(limits.current("GEOMETRY_CHECK_MAX_TOTAL_VERTICES"))
        self.seconds = float(limits.current("GEOMETRY_CHECK_SECONDS"))
        self.deadline = time.monotonic() + self.seconds
        self.walked = 0

    def oversize(self, geom) -> int | None:





        n = vertex_count(geom)
        if n > self.per_geometry:
            return n
        self.walked += n
        return None

    def exhausted(self) -> str | None:

        if time.monotonic() > self.deadline:
            return "time"
        if self.walked > self.total:
            return "vertices"
        return None

    def stop_reason(self, why: str) -> str:
        return "time budget" if why == "time" else "vertex budget"
