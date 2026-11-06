# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import os

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")

FILE_TYPES = {
    ".gpkg": "GeoPackage", ".geojson": "GeoJSON", ".json": "JSON",
    ".shp": "Shapefile", ".csv": "CSV", ".tif": "GeoTIFF", ".tiff": "GeoTIFF",
    ".kml": "KML", ".kmz": "KMZ", ".zip": "ZIP", ".gml": "GML",
    ".fgb": "FlatGeobuf", ".parquet": "Parquet",
    ".pdf": "PDF", ".docx": "Word", ".pptx": "PowerPoint", ".xlsx": "Excel",
    ".txt": "Text", ".md": "Markdown", ".html": "HTML", ".htm": "HTML", ".xml": "XML",
}


def file_type(item: dict) -> str:
    ext = os.path.splitext(str(item.get("name") or item.get("path") or ""))[1].lower()
    return FILE_TYPES.get(ext, ext.lstrip(".").upper() or "File")
