# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






try:
    from ._dev_overrides import dev_env
except ImportError:
    def dev_env(name: str, default: str = "") -> str:
        return default

__all__ = ["dev_env"]
