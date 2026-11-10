# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





try:
    from ._dev_trace import mark
except ImportError:
    def mark(name: str, **fields) -> None:
        return None

__all__ = ["mark"]
