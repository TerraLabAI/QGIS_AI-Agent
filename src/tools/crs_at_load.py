# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import math

from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY, QgsRasterLayer, QgsRectangle

from ..core.tool_registry import coded_fact
from .csv_loader import _project_candidates
from .processing_guards import _centre_in_area_of_use, degree_slack, fits_degrees

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
    numbers = f"x {_num(x0)} to {_num(x1)}, y {_num(y0)} to {_num(y1)}"
    name = layer.name()


    slack_x, slack_y = degree_slack(layer)
    degrees = fits_degrees(x0, x1, y0, y1, slack_x, slack_y)
    swapped_degrees = not degrees and fits_degrees(y0, y1, x0, x1, slack_y, slack_x)

    def projected_candidates(exclude: str = "") -> list:
        return [(c.authid(), origin) for c, origin in _project_candidates(extent)
                if c.authid() and c.authid() != exclude]

    if _not_georeferenced(layer, extent):
        return {"verdict": "not_georeferenced", "crs": "", "candidates": [],
                "detail": (f"'{name}' has no georeferencing: its extent is its own pixel grid ({numbers}), so it "
                           f"sits at 0,0 whatever CRS it is given.")}
    if not declared or not crs.isValid():
        if degrees:
            candidates = [(_WGS84, "the coordinates read as longitude/latitude")]
        elif swapped_degrees:
            candidates = [(_WGS84, "longitude/latitude, with x and y swapped")]
        else:
            candidates = projected_candidates()
        return {"verdict": "missing", "crs": "", "candidates": candidates,
                "detail": (f"'{name}' declares no CRS ({numbers}): QGIS cannot place it, and any measure or "
                           f"overlay on it would be wrong.")}
    label = _authid(crs)
    if crs.isGeographic():
        if degrees:
            inside = _in_area(crs, extent)
            if inside is False:
                return {"verdict": "outside_area_of_use", "crs": label, "candidates": [(_WGS84, "")] if
                        label != _WGS84 else [],
                        "detail": (f"'{name}' declares {label}, and its coordinates ({numbers}) fall outside the "
                                   f"area that CRS is defined for.")}
            return {}
        if swapped_degrees:
            return {"verdict": "swapped_axes", "crs": label, "candidates": [],
                    "detail": (f"'{name}' declares {label}, and its coordinates ({numbers}) fit it only with x and "
                               f"y swapped: latitude is in x.")}
        return {"verdict": "projected_declared_geographic", "crs": label, "candidates": projected_candidates(),
                "detail": (f"'{name}' declares {label}, a CRS in degrees, but its coordinates ({numbers}) cannot "
                           f"be degrees: they are projected, and the layer lands nowhere.")}
    inside = _in_area(crs, extent)
    if inside is not False:
        return {}
    if degrees:
        candidates = [(c, "the coordinates read as longitude/latitude")
                      for c in dict.fromkeys(filter(None, (_geographic_of(crs), _WGS84)))]
        return {"verdict": "degrees_declared_projected", "crs": label, "candidates": candidates,
                "detail": (f"'{name}' declares {label}, a CRS in metres, but its coordinates ({numbers}) are "
                           f"degrees: it sits near that CRS's origin, far from its ground.")}
    if _in_area(crs, _swapped(extent)) is True:
        return {"verdict": "swapped_axes", "crs": label, "candidates": [],
                "detail": (f"'{name}' declares {label}, and its coordinates ({numbers}) fall inside that CRS's area "
                           f"only with x and y swapped.")}
    return {"verdict": "outside_area_of_use", "crs": label, "candidates": projected_candidates(crs.authid()),
            "detail": (f"'{name}' declares {label}, and its coordinates ({numbers}) fall outside the area that CRS "
                       f"is defined for: drawn there, the layer is in the wrong place (drawing units, another "
                       f"zone or another grid).")}


def suggestion(found: dict, layer_name: str, cad: bool = False) -> str:

    kind = found.get("verdict")
    if kind == "not_georeferenced":
        return (f"Tell the user '{layer_name}' has no position yet; georeference it (georeference_raster) "
                "before any overlay or measure.")
    if kind == "swapped_axes":
        return (f"Swap the axes of '{layer_name}' (run_processing native:swapxy on a vector layer, then use the "
                "result) and tell the user; nothing about the CRS itself needs asking.")
    options = [f"{authid} ({why})" if why else authid for authid, why in found.get("candidates") or []]
    fix = ("add_data again on the drawing with crs=<the answer>" if cad
           else f"set_layer_crs on '{layer_name}' with the answer (it relabels, it does not reproject)")
    ask = ("ask the user once with ask_user"
           + (f", options {', '.join(options)} and allow_free_text true" if options
              else ", allow_free_text true (the numbers alone cannot tell one UTM zone or national grid from another)"))
    return (f"Before any measure, overlay or reprojection of '{layer_name}', {ask}, unless the user, the source or "
            f"its metadata already named the CRS; then {fix}.")





_VERDICT_FACT = {
    "not_georeferenced": coded_fact(hint="crs_not_georeferenced"),
    "missing": coded_fact(hint="crs_missing"),
    "swapped_axes": coded_fact(hint="crs_swapped_axes"),
    "projected_declared_geographic": coded_fact(hint="crs_projected_declared_geographic"),
    "degrees_declared_projected": coded_fact(hint="crs_degrees_declared_projected"),
    "outside_area_of_use": coded_fact(hint="crs_outside_area_of_use"),
}


def check_loaded(layer, crs_assigned: bool = False, cad: bool = False) -> dict:

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
    candidates = [authid for authid, _why in found["candidates"]]


    check = {"verdict": found["verdict"], "declared": found["crs"], "candidates": candidates,
             "layer": layer.name(), "variant": "candidates" if candidates else "none",
             **_VERDICT_FACT.get(found["verdict"], {})}
    return {
        "crs_check": check,
        "warning": found["detail"],
        "suggestion": suggestion(found, layer.name(), cad=cad),
    }


__all__ = ["check_loaded", "suggestion", "verdict"]
