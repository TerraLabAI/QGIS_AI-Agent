# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

from qgis.PyQt.QtCore import QTimer



OUTLINE_MAX = 2000

COUNT_MAX = 20000
_GLIDE_POLL_MS = 100
_GLIDE_POLLS = 40

_LAYER_KEYS = ("layer_name", "layer", "layer_id", "input_layer", "target_layer", "source")


def _project():
    from qgis.core import QgsProject

    return QgsProject.instance()


def _canvas():
    try:
        from qgis.utils import iface
    except ImportError:
        return None
    return iface.mapCanvas() if iface is not None else None


def layer_of(text):

    if not isinstance(text, str) or not text.strip():
        return None
    project = _project()
    layer = project.mapLayer(text)
    if layer is not None:
        return layer
    found = project.mapLayersByName(text)
    return found[0] if len(found) == 1 else None


def _ids(values) -> list:
    out = []
    for value in values if isinstance(values, (list, tuple)) else ():
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            out.append(value)
        elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
            out.append(int(value))
    return list(dict.fromkeys(out))


def _is_vector(layer) -> bool:
    from qgis.core import QgsVectorLayer

    return isinstance(layer, QgsVectorLayer)


def call_target(name: str, args, result=None) -> dict | None:





    if name == "delete_features":
        return None
    args = args if isinstance(args, dict) else {}
    result = result if isinstance(result, dict) else {}
    layer = None
    for key in ("layer_id", "output_layer_id"):
        layer = layer or layer_of(result.get(key))
    for value in (result.get("layer_ids") or result.get("layers_added") or [])[:1]:
        layer = layer or layer_of(value if isinstance(value, str) else
                                  (value.get("layer_id") if isinstance(value, dict) else None))
    from_args = layer is None
    for key in _LAYER_KEYS:
        layer = layer or layer_of(args.get(key))
    if layer is None:
        return None
    fids = None
    if from_args or layer_of(args.get("layer_name")) is layer:
        named = _ids(args.get("fids")) or _ids([u.get("fid") for u in args.get("updates") or []
                                                 if isinstance(u, dict)])
        fids = named or None
    if fids is None and "select" in str(name or "") and _is_vector(layer):
        fids = list(layer.selectedFeatureIds())[:COUNT_MAX] or None
    if fids is not None and _is_vector(layer):
        fids = _existing(layer, fids)
        if not fids:
            return None
    return {"layer_id": layer.id(), "fids": fids}


def _existing(layer, fids: list) -> list:
    from qgis.core import QgsFeatureRequest

    from ..core.qt_compat import enum_member

    request = (QgsFeatureRequest().setFilterFids(fids[:COUNT_MAX]).setNoAttributes()
               .setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry")))
    return [f.id() for f in layer.getFeatures(request)]


def proposal_of(name: str, args) -> dict | None:






    args = args if isinstance(args, dict) else {}
    kind = {"delete_features": "delete", "update_features": "overwrite",
            "remove_layer": "remove"}.get(str(name or ""))
    if kind is None:
        return None
    layer = layer_of(args.get("layer_name"))
    if layer is None:
        return None
    if kind == "remove":
        vector = _is_vector(layer)
        count = int(layer.featureCount()) if vector and not _remote(layer) else -1
        return {"kind": kind, "layer_id": layer.id(), "layer_name": layer.name(), "fids": None,
                "count": count, "memory": vector and layer.providerType() == "memory"}
    if not _is_vector(layer) or _remote(layer):
        return None
    expression = str(args.get("expression") or "") if kind == "delete" else ""
    if expression and not args.get("fids"):
        from ..core.feature_requests import matching_ids

        fids, _problem = matching_ids(layer, expression, COUNT_MAX)
        fids = (fids or [])[:COUNT_MAX]
    else:
        wanted = _ids(args.get("fids")) if kind == "delete" else \
            _ids([u.get("fid") for u in args.get("updates") or [] if isinstance(u, dict)])
        fids = _existing(layer, wanted) if wanted else []
    if not fids:
        return None
    return {"kind": kind, "layer_id": layer.id(), "layer_name": layer.name(), "fids": fids,
            "count": len(fids), "total": int(layer.featureCount()), "memory": False,
            "expression": expression}


def _remote(layer) -> bool:
    from ..core.layer_order import is_remote_vector

    return is_remote_vector(layer)


def _geometries(layer, fids, limit: int = OUTLINE_MAX, in_view: bool = True) -> list:


    from qgis.core import QgsCoordinateTransform, QgsFeatureRequest

    if not _is_vector(layer) or _remote(layer):
        return []
    request = QgsFeatureRequest().setNoAttributes().setLimit(limit)
    if fids is not None:
        request.setFilterFids(list(fids)[:COUNT_MAX])
    canvas = _canvas()
    if in_view and canvas is not None:
        try:
            transform = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), layer.crs(), _project())
            request.setFilterRect(transform.transformBoundingBox(canvas.extent()))
        except Exception:  # noqa: BLE001  # nosec B110
            pass
    return [f.geometry() for f in layer.getFeatures(request) if f.hasGeometry()]


def _extent_ring(layer):
    from qgis.core import QgsGeometry

    from ..core.layer_order import extent_known

    extent = extent_known(layer)
    if extent is None or extent.isNull() or extent.isEmpty():
        return []
    return [QgsGeometry.fromRect(extent)]


class Outline:




    def __init__(self, style: str = "peek"):
        self.style = style
        self._bands: list = []
        self.geometries: list = []
        self.crs = None

    def show(self, target: dict | None) -> bool:
        self.hide()
        canvas = _canvas()
        layer = _project().mapLayer(target["layer_id"]) if target else None
        if canvas is None or layer is None:
            return False
        geoms = _geometries(layer, target.get("fids"), in_view=self.style == "peek") \
            if _is_vector(layer) else []
        if not geoms and (target.get("fids") is None or not _is_vector(layer)):
            geoms = _extent_ring(layer)
        if not geoms:
            return False
        self.geometries, self.crs = geoms, layer.crs()
        kind = _kind(layer, geoms)
        for spec in _band_specs(self.style, kind):
            band = _band(canvas, kind, spec)
            for geom in geoms:
                band.addGeometry(geom, layer.crs(), False)
            band.updatePosition()
            band.update()
            self._bands.append(band)
        return True

    def hide(self) -> None:
        canvas = _canvas()
        for band in self._bands:
            try:
                band.reset()
                if canvas is not None:
                    canvas.scene().removeItem(band)
            except RuntimeError:
                pass
        self._bands = []

    def shown(self) -> bool:
        return bool(self._bands)


def _kind(layer, geoms):
    from qgis.core import QgsWkbTypes

    if _is_vector(layer):
        return layer.geometryType()
    return QgsWkbTypes.GeometryType.PolygonGeometry if geoms else layer.geometryType()


def _band_specs(style: str, kind) -> list:

    from qgis.core import QgsWkbTypes
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QColor

    from .style import ACCENT, RED

    polygon = kind == QgsWkbTypes.GeometryType.PolygonGeometry
    point = kind == QgsWkbTypes.GeometryType.PointGeometry
    if style == "peek":
        tint = QColor(ACCENT)
        tint.setAlpha(46 if polygon else 0)
        return [{"stroke": QColor(ACCENT), "fill": tint, "width": 2, "icon": "circle"}]
    red = QColor(RED)
    hatch = QColor(RED)
    hatch.setAlpha(200)
    tint = QColor(RED)
    tint.setAlpha(70)
    if point:
        return [{"stroke": red, "fill": tint, "width": 2, "icon": "x"}]
    if not polygon:
        return [{"stroke": red, "fill": tint, "width": 3, "dash": Qt.PenStyle.DashLine}]
    return [{"stroke": QColor(0, 0, 0, 0), "fill": tint, "width": 0},
            {"stroke": red, "fill": hatch, "brush": Qt.BrushStyle.BDiagPattern, "width": 2,
             "dash": Qt.PenStyle.DashLine}]


def _band(canvas, kind, spec):
    from qgis.gui import QgsRubberBand

    band = QgsRubberBand(canvas, kind)
    band.setStrokeColor(spec["stroke"])
    band.setFillColor(spec["fill"])
    band.setWidth(spec["width"])
    if spec.get("brush") is not None:
        band.setBrushStyle(spec["brush"])
    if spec.get("dash") is not None:
        band.setLineStyle(spec["dash"])
    if spec.get("icon"):
        band.setIcon(QgsRubberBand.IconType.ICON_X if spec["icon"] == "x"
                     else QgsRubberBand.IconType.ICON_CIRCLE)
        band.setIconSize(10)
    return band


def flash(layer_id: str, geoms=None, crs=None, colour: str = "") -> None:


    from qgis.core import QgsWkbTypes
    from qgis.PyQt.QtGui import QColor

    from .style import ACCENT

    canvas = _canvas()
    layer = _project().mapLayer(layer_id) if layer_id else None
    if canvas is None or (layer is None and not geoms):
        return
    crs = crs if crs is not None else layer.crs()
    if geoms is None:
        geoms = _geometries(layer, None) if _is_vector(layer) else _extent_ring(layer)
    if layer is not None and _is_vector(layer) and layer.geometryType() == QgsWkbTypes.GeometryType.LineGeometry:
        width = canvas.mapUnitsPerPixel() * 3
        geoms = [g.buffer(width, 4) for g in geoms]
    start = QColor(colour or ACCENT)
    end = QColor(start)
    start.setAlpha(230)
    end.setAlpha(0)
    if geoms:
        canvas.flashGeometries(geoms, crs, start, end, 3, 420)


_NATIVE_FLASHES = 2
_NATIVE_FLASH_MS = 450


def flash_native(layer_id: str) -> None:




    from qgis.core import QgsFeatureRequest
    from qgis.PyQt.QtGui import QColor

    canvas = _canvas()
    layer = _project().mapLayer(layer_id) if layer_id else None
    if canvas is None or layer is None:
        return
    start = QColor(canvas.selectionColor())
    start.setAlpha(140)
    end = QColor(start)
    end.setAlpha(0)
    if _is_vector(layer) and not _remote(layer):
        from qgis.core import QgsCoordinateTransform

        request = QgsFeatureRequest().setNoAttributes().setLimit(OUTLINE_MAX)
        try:
            transform = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), layer.crs(), _project())
            request.setFilterRect(transform.transformBoundingBox(canvas.extent()))
        except Exception:  # noqa: BLE001  # nosec B110
            pass
        ids = [f.id() for f in layer.getFeatures(request)]
        if ids:
            canvas.flashFeatureIds(layer, ids, start, end, _NATIVE_FLASHES, _NATIVE_FLASH_MS)
        return
    ring = _extent_ring(layer)
    if ring:
        canvas.flashGeometries(ring, layer.crs(), start, end, _NATIVE_FLASHES, _NATIVE_FLASH_MS)


def target_box(target: dict):

    from qgis.core import QgsCoordinateTransform, QgsFeatureRequest, QgsRectangle

    canvas = _canvas()
    layer = _project().mapLayer(target["layer_id"]) if target and canvas is not None else None
    if layer is None:
        return None
    fids = target.get("fids")
    if fids is not None and _is_vector(layer):
        box = QgsRectangle()
        box.setMinimal()
        request = QgsFeatureRequest().setFilterFids(list(fids)[:COUNT_MAX]).setNoAttributes()
        for feature in layer.getFeatures(request):
            if feature.hasGeometry():
                box.combineExtentWith(feature.geometry().boundingBox())
    else:
        from ..core.layer_order import extent_known

        box = extent_known(layer)
    if box is None or box.isNull() or box.width() < 0:
        return None
    try:
        transform = QgsCoordinateTransform(layer.crs(), canvas.mapSettings().destinationCrs(), _project())
        return transform.transformBoundingBox(box)
    except Exception:  # noqa: BLE001
        return None


def show_on_map(target: dict | None, geoms=None, crs=None, colour: str = "") -> str:



    from ..core import canvas_glide, follow

    box = target_box(target) if target else None
    if box is None:
        return ""
    moved = follow.move_to(box, whole=True)
    layer_id = target["layer_id"]
    polls = [0]

    def after_glide():
        polls[0] += 1
        if canvas_glide.gliding_now() and polls[0] < _GLIDE_POLLS:
            QTimer.singleShot(_GLIDE_POLL_MS, after_glide)
            return
        flash(layer_id, geoms if geoms is not None else _flash_geoms(target), crs, colour)

    if moved in ("", "kept"):
        after_glide()
    else:
        QTimer.singleShot(_GLIDE_POLL_MS, after_glide)
    return moved or "kept"


def _flash_geoms(target: dict):
    layer = _project().mapLayer(target["layer_id"])
    if layer is None or not _is_vector(layer):
        return None
    return _geometries(layer, target.get("fids"), in_view=False) or None
