# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI Agent by TerraLab: a chat panel that drives QGIS in plain language."""





def _drop_stale_modules() -> None:
    """Forget any module of this package a previous copy left in sys.modules."""








    import sys

    prefix = __name__ + "."
    for name in [key for key in sys.modules if key.startswith(prefix)]:
        sys.modules.pop(name, None)


_drop_stale_modules()


def classFactory(iface):
    from .src.plugin import AIAgentPlugin

    return AIAgentPlugin(iface)
