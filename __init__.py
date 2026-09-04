# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI Agent by TerraLab: a chat panel that drives QGIS in plain language."""





def classFactory(iface):
    from .src.plugin import AIAgentPlugin

    return AIAgentPlugin(iface)
