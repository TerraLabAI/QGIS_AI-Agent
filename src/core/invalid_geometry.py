# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

import os
import time


SCAN_LIMIT = 200_000


def _input_layers(parameters) -> list:
    from qgis.core import QgsProject, QgsVectorLayer

    found = []
    for value in parameters.values() if isinstance(parameters, dict) else ():
        for item in value if isinstance(value, (list, tuple)) else (value,):
            if isinstance(item, QgsVectorLayer):
                found.append(item)
            elif isinstance(item, str) and item:
                layer = QgsProject.instance().mapLayer(item)
                named = layer if layer is not None else next(iter(QgsProject.instance().mapLayersByName(item)), None)
                if named is None and os.path.isfile(item.split("|", 1)[0]):

                    opened = QgsVectorLayer(item, os.path.basename(item.split("|", 1)[0]), "ogr")
                    named = opened if opened.isValid() else None
                if isinstance(named, QgsVectorLayer):
                    found.append(named)
    return found


def first_invalid(parameters) -> dict:





    from qgis.core import QgsFeatureRequest

    from . import limits

    deadline = time.monotonic() + float(limits.current("GEOMETRY_CHECK_SECONDS"))
    read = 0
    for layer in _input_layers(parameters):
        for feature in layer.getFeatures(QgsFeatureRequest().setNoAttributes()):
            read += 1
            if read > SCAN_LIMIT or time.monotonic() > deadline:
                return {}
            geometry = feature.geometry()
            if not geometry.isNull() and not geometry.isGeosValid():
                return {"fid": feature.id(), "layer": layer.name()}
    return {}


def facts(parameters, context=None) -> dict:





    found = first_invalid(parameters)
    return dict(found, hint="invalid_geometry") if found else {}
