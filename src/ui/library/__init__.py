# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







__all__ = [
    "ExampleDetail",
    "ExamplesDialog",
]


def __getattr__(name):
    if name == "ExamplesDialog":
        from .dialog import ExamplesDialog

        return ExamplesDialog
    if name == "ExampleDetail":
        from .detail import ExampleDetail

        return ExampleDetail
    raise AttributeError(name)
