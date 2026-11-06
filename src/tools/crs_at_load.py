# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import math
import re

from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY, QgsRasterLayer, QgsRectangle

from ..core.tool_registry import coded_fact
from .csv_loader import _project_candidates
from .processing_crs_checks import _centre_in_area_of_use, degree_slack, fits_degrees

_WGS84 = "EPSG:4326"


def _num(value: float) -> str:
    return f"{value:.0f}" if abs(value) >= 1000 else f"{value:.6g}"


def _authid(crs) -> str:
    return crs.authid() or crs.description() or "a custom CRS"


def _geographic_of(crs) -> str:

    try:
        geographic = crs.toGeographicCrs()
        return geographic.authid() if geographic.isValid() else ""
    except Exception:  # noqa: BLE001
        return ""


def _not_georeferenced(layer, extent) -> bool:

    if not isinstance(layer, QgsRasterLayer):
        return False
    try:
        return (extent.xMinimum() == 0 and abs(extent.width() - layer.width()) < 1e-6
                and abs(extent.height() - layer.height()) < 1e-6 and extent.yMinimum() in (0, -layer.height()))
    except Exception:  # noqa: BLE001
        return False


def _in_area(crs, extent) -> bool | None:

    return _centre_in_area_of_use(crs, QgsPointXY(extent.center()))


def _swapped(extent) -> QgsRectangle:
    return QgsRectangle(extent.yMinimum(), extent.xMinimum(), extent.yMaximum(), extent.xMaximum())


def verdict(layer, crs, declared: bool = True) -> dict:




    try:
        extent = QgsRectangle(layer.extent())
    except Exception:  # noqa: BLE001
        return {}
    if extent.isNull() or not all(math.isfinite(v) for v in (extent.xMinimum(), extent.xMaximum(),
                                                              extent.yMinimum(), extent.yMaximum())):
        return {}
    x0, x1, y0, y1 = extent.xMinimum(), extent.xMaximum(), extent.yMinimum(), extent.yMaximum()
    numbers = {"x_min": _num(x0), "x_max": _num(x1), "y_min": _num(y0), "y_max": _num(y1)}


    slack_x, slack_y = degree_slack(layer)
    degrees = fits_degrees(x0, x1, y0, y1, slack_x, slack_y)
    swapped_degrees = not degrees and fits_degrees(y0, y1, x0, x1, slack_y, slack_x)

    def projected_candidates(exclude: str = "") -> list:
        return [c.authid() for c, _origin in _project_candidates(extent) if c.authid() and c.authid() != exclude]

    def found(kind: str, label: str, candidates: list, detail: str = "") -> dict:
        return {"verdict": kind, "crs": label, "candidates": candidates, "numbers": numbers,
                **({"detail_hint": detail} if detail else {})}

    if _not_georeferenced(layer, extent):
        return found("not_georeferenced", "", [], "crs_detail_not_georeferenced")
    if not declared or not crs.isValid():
        if degrees or swapped_degrees:
            candidates = [_WGS84]
        else:
            candidates = projected_candidates()
        return found("missing", "", candidates, "crs_detail_missing")
    label = _authid(crs)
    if crs.isGeographic():
        if degrees:
            inside = _in_area(crs, extent)
            if inside is False:
                return found("outside_area_of_use", label, [_WGS84] if label != _WGS84 else [])
            return {}
        if swapped_degrees:
            return found("swapped_axes", label, [], "crs_detail_swapped_axes")
        return found("projected_declared_geographic", label, projected_candidates(),
                     "crs_detail_projected_declared_geographic")
    inside = _in_area(crs, extent)
    if inside is not False:
        return {}
    if degrees:
        return found("degrees_declared_projected", label,
                     list(dict.fromkeys(filter(None, (_geographic_of(crs), _WGS84)))),
                     "crs_detail_degrees_declared_projected")
    if _in_area(crs, _swapped(extent)) is True:
        return found("swapped_axes", label, [], "crs_detail_swapped_axes_area")


    return found("outside_area_of_use", label, projected_candidates(crs.authid()))






_VERDICT_FACT = {
    "not_georeferenced": coded_fact(hint="crs_not_georeferenced"),
    "missing": coded_fact(hint="crs_missing"),
    "swapped_axes": coded_fact(hint="crs_swapped_axes"),
    "projected_declared_geographic": coded_fact(hint="crs_projected_declared_geographic"),
    "degrees_declared_projected": coded_fact(hint="crs_degrees_declared_projected"),
    "outside_area_of_use": coded_fact(hint="crs_outside_area_of_use"),
}


def check_loaded(layer, crs_assigned: bool = False) -> dict:



    try:
        provider = layer.dataProvider()
        provider_crs = provider.crs() if provider is not None else QgsCoordinateReferenceSystem()
    except Exception:  # noqa: BLE001
        provider_crs = QgsCoordinateReferenceSystem()
    declared = crs_assigned or provider_crs.isValid()
    crs = layer.crs() if (crs_assigned or not provider_crs.isValid()) else provider_crs
    found = verdict(layer, crs, declared=declared)
    if not found:
        return {}
    candidates = found["candidates"]


    check = {"verdict": found["verdict"], "declared": found["crs"], "candidates": candidates,
             "layer": layer.name(), "variant": "candidates" if candidates else "none",
             **found["numbers"], **_VERDICT_FACT.get(found["verdict"], {}),
             **({"detail_hint": found["detail_hint"]} if found.get("detail_hint") else {})}
    return {"crs_check": check}


def normalise_crs(layer) -> str:









    try:
        crs = layer.crs()
        if not crs.isValid() or crs.authid():
            return ""
        candidate = None
        name = (crs.description() or "").strip()
        if re.fullmatch(r"(?i)EPSG:\d{4,6}", name):
            candidate = QgsCoordinateReferenceSystem(name.upper())
        if candidate is None or not candidate.isValid():
            srsid = int(crs.findMatchingProj() or 0)
            candidate = QgsCoordinateReferenceSystem.fromSrsId(srsid) if srsid else None
        if candidate is None or not candidate.isValid() or not candidate.authid():
            return ""
        layer.setCrs(candidate)
        return candidate.authid()
    except Exception:  # noqa: BLE001
        return ""


__all__ = ["check_loaded", "normalise_crs", "verdict"]
