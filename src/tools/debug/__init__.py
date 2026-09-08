# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations


def install_capture():





    from .errors import install_error_capture
    from .network import install_network_logger

    install_network_logger()
    install_error_capture()
