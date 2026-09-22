# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import math
import os
import pathlib
import sqlite3

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject, QgsRectangle

from ..core.tool_registry import coded_fact



_ANCHOR_MAX_DEGREES = 30.0



_MARGIN_SHARE = 0.25
_MARGIN_MIN_DEGREES = 0.02

_MAX_ANCHORS = 8
_MAX_CANDIDATES = 400
_SHOWN = 3

_WGS84 = "EPSG:4326"
_cache: dict = {}


def _proj_db() -> str:

    try:
        from qgis.core import QgsProjUtils
        paths = list(QgsProjUtils.searchPaths())
    except Exception:  # noqa: BLE001
        paths = []
    paths += [os.environ.get(v, "") for v in ("PROJ_DATA", "PROJ_LIB")]
    for folder in paths:
        path = os.path.join(folder, "proj.db") if folder else ""
        if path and os.path.isfile(path):
            return path
    return ""


def _transforms_here(crs) -> bool:







    try:
        from qgis.core import QgsDatumTransform
        operations = QgsDatumTransform.operations(crs, QgsCoordinateReferenceSystem(_WGS84))
    except Exception:  # noqa: BLE001
        return True
    return not operations or bool(operations[0].isAvailable)


def _to_wgs84(crs, rect: QgsRectangle) -> QgsRectangle | None:
    try:
        box = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem(_WGS84),
                                     QgsProject.instance()).transformBoundingBox(rect)
    except Exception:  # noqa: BLE001
        return None
    values = (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())
    if box.isNull() or not all(math.isfinite(v) for v in values):
        return None
    if box.yMinimum() < -90.5 or box.yMaximum() > 90.5:
        return None
    return box


def anchors(exclude_ids: tuple = ()) -> list:

    found = []
    for layer in QgsProject.instance().mapLayers().values():
        if layer.id() in exclude_ids:
            continue
        try:
            crs, extent = layer.crs(), layer.extent()
        except RuntimeError:
            continue
        if not crs.isValid() or extent.isNull():
            continue
        box = _to_wgs84(crs, extent)
        if box is None or box.width() > _ANCHOR_MAX_DEGREES or box.height() > _ANCHOR_MAX_DEGREES:
            continue
        found.append((layer.name(), box))
    found.sort(key=lambda item: item[1].width() * item[1].height())
    return found[:_MAX_ANCHORS]


def _margin(box: QgsRectangle) -> float:
    return max(_MARGIN_MIN_DEGREES, _MARGIN_SHARE * max(box.width(), box.height()))


def _km(a: QgsPointXY, b: QgsPointXY) -> float:

    la1, la2 = math.radians(a.y()), math.radians(b.y())
    dla, dlo = la2 - la1, math.radians(b.x() - a.x())
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def landing(crs, extent: QgsRectangle, anchor_list: list) -> tuple[list, float | None]:

    box = _to_wgs84(crs, extent)
    if box is None:
        return [], None
    centre = box.center()
    on, nearest = [], None
    for name, anchor in anchor_list:
        km = _km(QgsPointXY(centre), QgsPointXY(anchor.center()))
        nearest = km if nearest is None else min(nearest, km)
        grown = QgsRectangle(anchor)
        grown.grow(_margin(anchor))
        if grown.contains(QgsPointXY(centre)):
            on.append(name)
    return on, nearest


def _codes_near(anchor_list: list) -> list:

    path = _proj_db()
    if not path:
        return []
    query = (
        "SELECT DISTINCT pc.auth_name, pc.code, pc.name FROM projected_crs pc "
        "JOIN usage u ON u.object_table_name = 'projected_crs' AND u.object_auth_name = pc.auth_name "
        "AND u.object_code = pc.code "
        "JOIN extent e ON e.auth_name = u.extent_auth_name AND e.code = u.extent_code "
        "WHERE pc.auth_name = 'EPSG' AND pc.deprecated = 0 AND e.south_lat <= ? AND e.north_lat >= ? "
        "AND ((e.west_lon <= e.east_lon AND e.west_lon <= ? AND e.east_lon >= ?) "
        "OR (e.west_lon > e.east_lon AND (e.west_lon <= ? OR e.east_lon >= ?)))"
    )
    out: dict = {}
    try:
        con = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
    except (sqlite3.Error, ValueError):
        return []
    try:
        for _name, box in anchor_list:
            rows = con.execute(query, (box.yMaximum(), box.yMinimum(), box.xMaximum(), box.xMinimum(),
                                       box.xMaximum(), box.xMinimum())).fetchall()
            for auth, code, name in rows:
                out.setdefault(f"{auth}:{code}", name)
                if len(out) >= _MAX_CANDIDATES:
                    break
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return list(out.items())


def candidates(extent: QgsRectangle, anchor_list: list, exclude: str = "") -> list:





    if extent.isNull() or not anchor_list:
        return []
    key = (tuple(round(v, 3) for v in (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(),
                                        extent.yMaximum())),
           tuple((n, b.toString(6)) for n, b in anchor_list), exclude)
    if key in _cache:
        return _cache[key]
    found = []
    for authid, name in _codes_near(anchor_list):
        if authid == exclude:
            continue
        crs = QgsCoordinateReferenceSystem(authid)
        if not crs.isValid() or not _transforms_here(crs):
            continue
        on, km = landing(crs, extent, anchor_list)
        if on:
            try:
                inverted = crs.hasAxisInverted()
            except Exception:  # noqa: BLE001
                inverted = True
            found.append({"crs": authid, "name": name, "lands_on": on[:3], "km": round(km, 2),
                          "_rank": (round(km, 2), inverted, int(authid.split(":")[1]))})
    found.sort(key=lambda c: c["_rank"])
    for c in found:
        del c["_rank"]
    if len(_cache) > 64:
        _cache.clear()
    _cache[key] = found
    return found


def sentence(found: list) -> str:

    parts = []
    for c in found[:_SHOWN]:
        on = ", ".join(f"'{n}'" for n in c["lands_on"])
        parts.append(f"{c['crs']} ({c['name']}) puts them on {on}")
    return "; ".join(parts)


def layer_check(layer) -> dict:





    try:
        crs, extent = layer.crs(), layer.extent()
    except Exception:  # noqa: BLE001
        return {}
    if not crs.isValid() or extent.isNull():
        return {}
    own = _to_wgs84(crs, extent)
    if own is not None and (own.width() > _ANCHOR_MAX_DEGREES or own.height() > _ANCHOR_MAX_DEGREES):
        return {}
    anchor_list = anchors(exclude_ids=(layer.id(),))
    if not anchor_list:
        return {}
    on, km = landing(crs, extent, anchor_list)
    if on:
        return {}
    found = candidates(extent, anchor_list, exclude=crs.authid())
    if not found:
        return {}
    name, best = layer.name(), found[0]
    where = ", ".join(f"'{n}'" for n in best["lands_on"])
    far = f"{km:.0f} km from every other layer of the project" if km is not None else "nowhere"



    check = {"verdict": "off_project", "layer": name, "declared": crs.authid(),
             "km_from_project": round(km) if km is not None else None,
             "candidates": [c["crs"] for c in found[:_SHOWN]],
             **coded_fact(hint="crs_off_project_layer", best=best["crs"],
                          km=round(km) if km is not None else None, lands_on=where)}
    return {
        "crs_check": check,
        "warning": (f"Read in its declared {crs.authid() or 'CRS'}, '{name}' lies {far}. Its coordinates fit "
                    f"another CRS: {sentence(found)}. The declared CRS is likely wrong."),
    }


__all__ = ["anchors", "candidates", "landing", "layer_check", "sentence"]
