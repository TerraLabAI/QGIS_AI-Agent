# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

import os
import sys


def main(argv: list[str]) -> int:
    path, out, width, height, dpi = argv[1], argv[2], int(argv[3]), int(argv[4]), float(argv[5])
    from osgeo import gdal

    gdal.UseExceptions()

    gdal.SetConfigOption("GDAL_PAM_ENABLED", "NO")
    dataset = gdal.OpenEx(path, gdal.OF_RASTER, open_options=[f"DPI={max(dpi, 1.0):.3f}"])
    staged = out + ".part"
    gdal.Translate(staged, dataset, format="PNG", width=width, height=height)
    dataset = None  # noqa: F841
    os.replace(staged, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
