# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a handler may ask GEOS on the Qt main thread before it stops."""












from __future__ import annotations

import time

from . import limits


def vertex_count(geom) -> int:
    """Vertices in a geometry; 0 when it cannot say, which reads as small."""
    try:
        inner = geom.constGet()
        return int(inner.nCoordinates()) if inner is not None else 0
    except Exception:  # noqa: BLE001 - a geometry with no count is operated on as if small
        return 0


class VertexBudget:
    """One call's allowance: per geometry, in total, and in seconds."""

    def __init__(self) -> None:
        self.per_geometry = int(limits.current("GEOMETRY_CHECK_MAX_VERTICES"))
        self.total = int(limits.current("GEOMETRY_CHECK_MAX_TOTAL_VERTICES"))
        self.seconds = float(limits.current("GEOMETRY_CHECK_SECONDS"))
        self.deadline = time.monotonic() + self.seconds
        self.walked = 0

    def oversize(self, geom) -> int | None:
        """The vertex count when the geometry is past the per-geometry cap, else None."""




        n = vertex_count(geom)
        if n > self.per_geometry:
            return n
        self.walked += n
        return None

    def exhausted(self) -> str | None:
        """'time' or 'vertices' when the call should stop, None while it may go on."""
        if time.monotonic() > self.deadline:
            return "time"
        if self.walked > self.total:
            return "vertices"
        return None

    def stop_reason(self, why: str) -> str:
        return "time budget" if why == "time" else "vertex budget"
