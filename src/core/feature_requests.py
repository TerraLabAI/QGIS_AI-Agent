# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small provider requests, built on the Qt main thread."""




from __future__ import annotations

from qgis.core import QgsFeatureRequest

from .qt_compat import enum_member


def feature_request(*, attributes=None, geometry: bool = True, limit: int | None = None,
                    expression=None, fields=None):
    request = QgsFeatureRequest()
    if expression is not None:
        columns = list(expression.referencedColumns())
        attributes = None if "*" in columns else columns
        geometry = expression.needsGeometry()
    if not geometry:
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    if attributes is not None:
        if fields is None:
            request.setSubsetOfAttributes(attributes)
        else:
            request.setSubsetOfAttributes(attributes, fields)
    if limit is not None:
        request.setLimit(max(0, int(limit)))
    return request


def first_feature(layer, request):
    """One feature, releasing the provider connection immediately on every path."""
    request.setLimit(1)
    iterator = layer.getFeatures(request)
    try:
        return next(iterator, None)
    finally:
        iterator.close()
