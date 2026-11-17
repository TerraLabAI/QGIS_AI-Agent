# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





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

    request.setLimit(1)
    iterator = layer.getFeatures(request)
    try:
        return next(iterator, None)
    finally:
        iterator.close()


def matching_ids(layer, text: str, limit: int) -> tuple[list | None, str]:





    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils

    expression = QgsExpression(text)
    if expression.hasParserError():
        return None, expression.parserErrorString()
    context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    request = QgsFeatureRequest().setFilterExpression(text).setExpressionContext(context)
    request.setLimit(max(0, int(limit)) + 1)
    return [f.id() for f in layer.getFeatures(request)], ""
