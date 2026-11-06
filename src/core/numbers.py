# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







import math


def as_float(value, default=None):
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def as_finite(value, default=None):

    number = as_float(value)
    return number if number is not None and math.isfinite(number) else default


def as_int(value, default=None):
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default
