# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
































from __future__ import annotations

import math
import re
import time
from contextlib import contextmanager, nullcontext

from qgis.core import (
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QEvent, QObject, QTimer

from . import canvas_glide, tuning
from .layer_order import is_backdrop, reading_back
from .logger import log, log_warning
from .qt_compat import enum_member, qgis_iface
from .tool_registry import NAMED_LAYER, spec
from .vsi import streamed_in_place



PAD_SHARE = 0.25


CONTAINED_SHARE = 0.1


POINT_SHARE = 0.08





AREA_OF_USE_MARGIN = 1.0

SETTLE_MS = 180



_MAX_PER_SIGNAL = 2000







_SHIPPED_FEEL = {
    "pad_share": PAD_SHARE, "contained_share": CONTAINED_SHARE,
    "point_share": POINT_SHARE, "settle_ms": SETTLE_MS,
}
FEEL: dict = dict(_SHIPPED_FEEL)


def _refresh_feel() -> None:

    global FEEL
    FEEL = {name: tuning.number("follow", name, shipped) for name, shipped in _SHIPPED_FEEL.items()}


tuning.subscribe(_refresh_feel)


def _canvas():
    iface = qgis_iface()
    if iface is None:
        return None
    try:
        return iface.mapCanvas()
    except Exception:  # noqa: BLE001
        return None


def _to_canvas_crs(rect, layer, canvas) -> QgsRectangle | None:











    if rect is None or rect.isNull():
        return None
    try:
        source = layer.crs()
        target = canvas.mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001
        return None
    if not source.isValid() or not target.isValid():
        return None
    if source == target:
        return QgsRectangle(rect)
    try:
        transform = QgsCoordinateTransform(source, target, QgsProject.instance())
        return transform.transformBoundingBox(QgsRectangle(rect))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Follow: no transform from {source.authid()} to {target.authid()}: {exc}")
        return None


def _deleted(obj) -> bool:





    try:
        import sip
    except ImportError:
        try:
            from qgis.PyQt import sip  # type: ignore[no-redef]
        except ImportError:
            return False
    try:
        return bool(sip.isdeleted(obj))
    except (TypeError, ValueError, RuntimeError):
        return False


def _grown(rect: QgsRectangle, share: float) -> QgsRectangle:

    pad = max(rect.width(), rect.height()) * share
    out = QgsRectangle(rect)
    out.grow(pad if pad > 0 else 0.0)
    return out


class EditWatcher:









    def __init__(self):
        self._rect: QgsRectangle | None = None



        self._crs = None
        self._layers: list = []
        self._project = None
        self._watching = False
        self._repaint_pending = False


    def start(self) -> None:

        self.stop()
        self._rect = None
        self._crs = None
        project = QgsProject.instance()
        if project is None:
            return
        self._project = project
        try:
            project.layersAdded.connect(self._on_layers_added)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Follow: cannot watch new layers: {exc}")
            self._project = None


        if self._project is not None:
            try:
                project.layersWillBeRemoved.connect(self._on_layers_removed)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Follow: cannot watch removals: {exc}")
            try:
                project.layersRemoved.connect(self._on_layers_gone)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Follow: cannot repaint after removals: {exc}")
        for layer in list(project.mapLayers().values()):
            self._watch(layer)
        self._watching = True

    def stop(self) -> None:

        if self._project is not None:
            for signal_name, slot in (("layersAdded", self._on_layers_added),
                                      ("layersWillBeRemoved", self._on_layers_removed),
                                      ("layersRemoved", self._on_layers_gone)):
                signal = getattr(self._project, signal_name, None)
                try:
                    if signal is not None:
                        signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            self._project = None
        for layer, bound in self._layers:
            if _deleted(layer):
                continue
            for signal, slot in bound:
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._layers = []
        self._watching = False

    def _on_layers_gone(self, _layer_ids) -> None:









        if self._repaint_pending:
            return
        self._repaint_pending = True

        def repaint():
            self._repaint_pending = False
            canvas = _canvas()
            if canvas is None or _deleted(canvas):
                return
            try:
                canvas.refresh()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Follow: cannot repaint after a removal: {exc}")

        QTimer.singleShot(0, repaint)

    def _on_layers_removed(self, layer_ids) -> None:

        ids = {str(i) for i in (layer_ids or ())}
        if not ids:
            return
        kept = []
        for layer, bound in self._layers:
            if _deleted(layer):
                continue
            try:
                mine = layer.id() in ids
            except (RuntimeError, AttributeError):
                continue
            if not mine:
                kept.append((layer, bound))
                continue
            for signal, slot in bound:
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._layers = kept

    def take(self) -> QgsRectangle | None:

        rect = self.peek()
        self._rect, self._crs = None, None
        return rect

    def peek(self) -> QgsRectangle | None:
        if self._rect is None:
            return None
        return self._in_canvas_crs(_canvas())

    def _in_canvas_crs(self, canvas) -> QgsRectangle | None:

        if canvas is None or self._crs is None:
            return QgsRectangle(self._rect)
        return _rect_from_crs(self._rect, self._crs, canvas)


    def _connections(self, layer):








        pairs = []
        for name, make in (("geometryChanged", self._geometry_slot),
                           ("featureAdded", self._added_slot),
                           ("committedFeaturesAdded", self._committed_features_slot),
                           ("committedGeometriesChanges", self._committed_geometries_slot)):
            signal = getattr(layer, name, None)
            if signal is not None:
                pairs.append((signal, make(layer)))
        return pairs

    def _watch(self, layer) -> None:
        if not isinstance(layer, QgsVectorLayer) or any(known is layer for known, _ in self._layers):
            return
        bound = []
        for signal, slot in self._connections(layer):
            try:
                signal.connect(slot)
                bound.append((signal, slot))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Follow: cannot watch {layer.name()}: {exc}")
        if bound:
            self._layers.append((layer, bound))

    def _on_layers_added(self, layers) -> None:











        canvas = _canvas()
        for layer in layers or []:
            self._watch(layer)
            if canvas is None or reading_back() or is_backdrop(layer) or streamed_in_place(layer):
                continue
            try:
                extent = layer.extent()
            except Exception:  # noqa: BLE001  # nosec B112
                continue
            self._add(_to_canvas_crs(extent, layer, canvas))



    def _geometry_slot(self, layer):
        def on_changed(_fid, geometry):
            self._add_geometry(geometry, layer)
        return on_changed

    def _added_slot(self, layer):
        def on_added(fid):
            try:
                feature = layer.getFeature(fid)
                geometry = feature.geometry() if feature is not None else None
            except Exception:  # noqa: BLE001
                return
            self._add_geometry(geometry, layer)
        return on_added

    def _committed_features_slot(self, layer):
        def on_committed(_layer_id, features):
            for feature in list(features or [])[:_MAX_PER_SIGNAL]:
                try:
                    self._add_geometry(feature.geometry(), layer)
                except Exception:  # noqa: BLE001  # nosec B112
                    continue
        return on_committed

    def _committed_geometries_slot(self, layer):
        def on_committed(_layer_id, geometries):
            for geometry in list((geometries or {}).values())[:_MAX_PER_SIGNAL]:
                self._add_geometry(geometry, layer)
        return on_committed

    def _add_geometry(self, geometry, layer) -> None:
        if geometry is None:
            return
        try:
            if geometry.isNull() or geometry.isEmpty():
                return
            box = geometry.boundingBox()
        except Exception:  # noqa: BLE001
            return
        canvas = _canvas()
        if canvas is None or layer is None:
            self._add(QgsRectangle(box))
            return
        self._add(_to_canvas_crs(box, layer, canvas))

    def _add(self, rect) -> None:
        if rect is None or rect.isNull():
            return
        canvas = _canvas()
        crs = _canvas_crs(canvas) if canvas is not None else None
        if self._rect is None:
            self._rect = QgsRectangle(rect)
        else:
            if crs is not None and self._crs is not None and crs != self._crs:
                self._rect = self._in_canvas_crs(canvas)
                if self._rect is None:
                    self._rect = QgsRectangle(rect)
                    self._crs = crs
                    return
            self._rect.combineExtentWith(rect)
        self._crs = crs


def same_view(first, second) -> bool:

    if first is None or second is None:
        return False
    try:
        tolerance = max(second.width(), second.height()) * 1e-6
        return (abs(first.xMinimum() - second.xMinimum()) <= tolerance
                and abs(first.yMinimum() - second.yMinimum()) <= tolerance
                and abs(first.xMaximum() - second.xMaximum()) <= tolerance
                and abs(first.yMaximum() - second.yMaximum()) <= tolerance)
    except Exception:  # noqa: BLE001
        return False


def _restore(canvas, rect, crs_before) -> None:








    if canvas is None or rect is None:
        return
    target = QgsRectangle(rect)
    try:
        crs_now = canvas.mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001
        crs_now = None
    if (crs_before is not None and crs_now is not None and crs_before.isValid()
            and crs_now.isValid() and crs_before != crs_now):
        try:
            transform = QgsCoordinateTransform(crs_before, crs_now, QgsProject.instance())
            target = transform.transformBoundingBox(target)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Follow: cannot restore the view across CRS: {exc}")
            return
    try:
        if same_view(canvas.extent(), target):
            return
        canvas.setExtent(target)
        canvas.refresh()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Follow: view not restored: {exc}")


@contextmanager
def view_kept(canvas=None):



























    canvas = canvas if canvas is not None else _canvas()
    before = None
    crs_before = None
    was_empty = False
    if canvas is not None:
        try:
            before = QgsRectangle(canvas.extent())
            crs_before = canvas.mapSettings().destinationCrs()
        except Exception:  # noqa: BLE001
            before = None
        try:
            was_empty = QgsProject.instance().count() == 0
        except Exception:  # noqa: BLE001
            was_empty = False
    if before is not None and (before.isNull() or before.isEmpty()):
        before = None
    held = _first_layer_setup_held(canvas, before) if was_empty else None
    try:
        with held or nullcontext():
            yield
    finally:
        if before is not None:
            _restore(canvas, before, crs_before)
        if canvas is not None and was_empty and held is None:
            _undo_first_layer_frame(canvas)


def _chosen_crs(crs) -> bool:

    from qgis.core import QgsCoordinateReferenceSystem, QgsSettings

    if crs is None or not crs.isValid():
        return False
    try:
        default = QgsSettings().value("/projections/defaultProjectCrs", "EPSG:4326",
                                      section=enum_member(QgsSettings, "Section", "App"))
    except Exception:  # noqa: BLE001
        default = "EPSG:4326"
    return crs != QgsCoordinateReferenceSystem(str(default or "EPSG:4326"))


def _first_layer_setup_held(canvas, before):









    iface = qgis_iface()
    try:
        bridge = iface.layerTreeCanvasBridge() if iface is not None else None
        crs = QgsProject.instance().crs()
    except Exception:  # noqa: BLE001
        return None
    if bridge is None or canvas is None or not _chosen_crs(crs):
        return None
    return _held(bridge, canvas, crs, before is None)


@contextmanager
def _held(bridge, canvas, crs, frame_area: bool):
    from qgis.core import QgsCoordinateReferenceSystem

    was_on = bridge.autoSetupOnFirstLayer()
    bridge.setAutoSetupOnFirstLayer(False)
    try:
        yield
    finally:
        try:
            bridge.setCanvasLayers()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Follow: layer tree bridge not run after the add: {exc}")
        bridge.setAutoSetupOnFirstLayer(was_on)
        if frame_area and QgsProject.instance().count() > 0:
            try:
                area = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs,
                                              QgsProject.instance()).transformBoundingBox(crs.bounds())
                if not area.isNull() and not area.isEmpty():
                    canvas.setExtent(area)
                    canvas.refresh()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Follow: cannot frame {crs.authid()}'s area of use: {exc}")


FIRST_FRAME_GUARD_MS = 1500


FRAME_SHARE = 0.8


def _is_framing(now: QgsRectangle, full: QgsRectangle) -> bool:
    if full.isNull() or full.isEmpty() or not now.contains(full):
        return False
    return full.width() >= now.width() * FRAME_SHARE or full.height() >= now.height() * FRAME_SHARE


def _undo_first_layer_frame(canvas) -> None:









    state = {"kept": None, "crs": None, "chosen": False, "done": False}

    def finish():
        if state["done"]:
            return
        state["done"] = True
        try:
            canvas.extentsChanged.disconnect(on_change)
        except (TypeError, RuntimeError):
            pass

    def on_change():
        if state["done"]:
            return
        try:
            now = QgsRectangle(canvas.extent())
            crs_now = canvas.mapSettings().destinationCrs()
            full = QgsRectangle(canvas.fullExtent())
        except Exception:  # noqa: BLE001
            finish()
            return
        if now.isNull() or now.isEmpty():
            return
        if not _is_framing(now, full):
            state["kept"], state["crs"], state["chosen"] = now, crs_now, True
            return
        kept = state["kept"]
        finish()
        if not state["chosen"] or kept is None:
            return


        QTimer.singleShot(0, lambda: _restore(canvas, kept, state["crs"]))

    try:
        canvas.extentsChanged.connect(on_change)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Follow: cannot guard the first layer's framing: {exc}")
        return
    QTimer.singleShot(FIRST_FRAME_GUARD_MS, finish)








ADD_FRAME_GUARD_MS = 1200


def keep_view_over_add(canvas=None) -> None:

    canvas = canvas or _canvas()
    if canvas is None:
        return
    if QgsProject.instance().count() == 0:
        return
    try:
        kept = QgsRectangle(canvas.extent())
        crs = canvas.mapSettings().destinationCrs()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Follow: cannot read the view to keep: {exc}")
        return
    if kept.isNull() or kept.isEmpty():
        return
    state = {"done": False}

    def finish():
        if state["done"]:
            return
        state["done"] = True
        try:
            canvas.extentsChanged.disconnect(on_change)
        except (TypeError, RuntimeError):
            pass

    def on_change():
        if state["done"]:
            return
        try:
            now = QgsRectangle(canvas.extent())
            full = QgsRectangle(canvas.fullExtent())
        except Exception:  # noqa: BLE001
            finish()
            return
        if now.isNull() or now.isEmpty() or not _is_framing(now, full):
            return
        finish()

        QTimer.singleShot(0, lambda: _restore(canvas, kept, crs))

    try:
        canvas.extentsChanged.connect(on_change)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Follow: cannot guard the added layer's framing: {exc}")
        return
    QTimer.singleShot(ADD_FRAME_GUARD_MS, finish)


def _area(rect) -> float:
    return max(0.0, float(rect.width())) * max(0.0, float(rect.height()))


def move_to(rect, canvas=None) -> str:






    if rect is None:
        return ""
    canvas = canvas if canvas is not None else _canvas()
    if canvas is None:
        return ""
    try:
        current = QgsRectangle(canvas.extent())
    except Exception:  # noqa: BLE001
        return ""
    target = QgsRectangle(rect)
    if target.isNull():
        return ""
    if target.width() <= 0 and target.height() <= 0:
        pad = max(current.width(), current.height()) * FEEL["point_share"] / 2
        target.grow(pad)
    padded = _grown(target, FEEL["pad_share"])

    if current.contains(padded):
        return "kept"
    if target.contains(current) and _area(current) >= _area(target) * FEEL["contained_share"]:
        return "kept"
    picture = canvas_glide.take(canvas)
    if padded.width() <= current.width() and padded.height() <= current.height():

        half_w, half_h = current.width() / 2, current.height() / 2
        cx, cy = target.center().x(), target.center().y()
        canvas.setExtent(QgsRectangle(cx - half_w, cy - half_h, cx + half_w, cy + half_h))
        canvas.refresh()
        action = "reframed" if hold_view(canvas) else "panned"
    else:
        canvas.setExtent(padded)
        canvas.refresh()
        action = "reframed" if hold_view(canvas) else "zoomed"
    canvas_glide.start(canvas, picture)
    return action


def _crs_holds(crs, geo_rect) -> bool:



    try:
        bounds = QgsRectangle(crs.bounds())
    except Exception:  # noqa: BLE001
        return True
    if bounds.isNull() or bounds.isEmpty():
        return True


    dx, dy = bounds.width() * AREA_OF_USE_MARGIN, bounds.height() * AREA_OF_USE_MARGIN
    widened = QgsRectangle(bounds.xMinimum() - dx, bounds.yMinimum() - dy,
                           bounds.xMaximum() + dx, bounds.yMaximum() + dy)
    return widened.contains(geo_rect)


def hold_view(canvas=None, prefer=None, target=None) -> dict | None:




















    from qgis.core import QgsCoordinateReferenceSystem

    canvas = canvas if canvas is not None else _canvas()
    if canvas is None:
        return None
    current = _canvas_crs(canvas)
    if current is None or not current.isValid():
        return None
    geographic = QgsCoordinateReferenceSystem("EPSG:4326")
    project = QgsProject.instance()
    geo_rect = None
    if target is not None:
        rect, crs = target
        try:
            geo_rect = QgsCoordinateTransform(crs, geographic, project).transformBoundingBox(QgsRectangle(rect))
        except Exception:  # noqa: BLE001
            geo_rect = None
    if geo_rect is None:
        try:
            geo_rect = QgsCoordinateTransform(current, geographic, project).transformBoundingBox(
                QgsRectangle(canvas.extent()))
        except Exception:  # noqa: BLE001
            geo_rect = None
    if geo_rect is None or geo_rect.isNull():
        return None
    if _crs_holds(current, geo_rect):
        return None
    chosen = None
    for candidate in (prefer, QgsCoordinateReferenceSystem("EPSG:3857"), geographic):
        if candidate is not None and candidate.isValid() and _crs_holds(candidate, geo_rect):
            chosen = candidate
            break
    if chosen is None or chosen == current:
        return None
    try:
        frame = QgsCoordinateTransform(geographic, chosen, project).transformBoundingBox(geo_rect)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Follow: cannot frame the view in {chosen.authid()}: {exc}")
        return None
    project.setCrs(chosen)
    if _canvas_crs(canvas) != chosen:
        canvas.setDestinationCrs(chosen)
    canvas.setExtent(frame)
    canvas.refresh()
    return {"from": current.authid(), "to": chosen.authid(),
            "why": (f"The view was outside the area {current.authid()} is defined for, where QGIS draws it "
                    f"distorted and its vector tile renderer can crash, so the project now uses "
                    f"{chosen.authid()}.")}


def layers_rect(layer_ids, canvas=None) -> QgsRectangle | None:














    canvas = canvas if canvas is not None else _canvas()
    if canvas is None:
        return None
    project = QgsProject.instance()
    if project is None:
        return None
    try:
        visible = {layer.id() for layer in canvas.layers()}
    except Exception:  # noqa: BLE001
        visible = None
    out: QgsRectangle | None = None
    for layer_id in layer_ids or ():
        layer = project.mapLayer(layer_id) if isinstance(layer_id, str) else None
        if layer is None or is_backdrop(layer) or streamed_in_place(layer):
            continue
        if visible is not None and layer.id() not in visible:
            continue
        rect = _canvas_extent_of(layer, canvas)
        if rect is None:
            continue
        if out is None:
            out = QgsRectangle(rect)
        else:
            out.combineExtentWith(rect)
    return out


def _canvas_crs(canvas):
    try:
        return canvas.mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001
        return None


def _rect_from_crs(rect, source, canvas) -> QgsRectangle | None:

    if rect is None or rect.isNull():
        return None
    target = _canvas_crs(canvas)
    if source is None or target is None or not source.isValid() or not target.isValid():
        return QgsRectangle(rect)
    if source == target:
        return QgsRectangle(rect)
    try:
        transform = QgsCoordinateTransform(source, target, QgsProject.instance())
        return transform.transformBoundingBox(QgsRectangle(rect))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Follow: no transform from {source.authid()} to {target.authid()}: {exc}")
        return None


def _looks_like_degrees(rect) -> bool:
    return (-180.0 <= rect.xMinimum() <= 180.0 and -180.0 <= rect.xMaximum() <= 180.0
            and -90.0 <= rect.yMinimum() <= 90.0 and -90.0 <= rect.yMaximum() <= 90.0)


def _crs(authid: str):
    try:
        from qgis.core import QgsCoordinateReferenceSystem
    except ImportError:
        return None
    try:
        crs = QgsCoordinateReferenceSystem(str(authid))
    except Exception:  # noqa: BLE001
        return None
    return crs if crs.isValid() else None


def _canvas_extent_of(layer, canvas):

    try:
        rect = _to_canvas_crs(layer.extent(), layer, canvas)
    except Exception:  # noqa: BLE001
        return None
    if rect is None or rect.isNull() or rect.isEmpty():
        return None
    return rect


def _crs_of(layer):

    try:
        crs = layer.crs()
    except Exception:  # noqa: BLE001
        return None
    return crs if crs is not None and crs.isValid() else None


def _named_layer_crs(args: dict):





    project = QgsProject.instance()
    if project is None:
        return None
    for key in ("layer_name", "layer", "target_layer", "input", "input_layer"):
        value = args.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            layers = project.mapLayersByName(value)
        except Exception:  # noqa: BLE001
            layers = []
        layer = layers[0] if layers else project.mapLayer(value)
        if layer is None:
            continue
        crs = _crs_of(layer)
        if crs is not None:
            return crs
    return None


def _xy_crs(sources: tuple, args: dict):





    for source in sources:
        if source == NAMED_LAYER:
            return _named_layer_crs(args)
        if args.get(source):
            return _crs(args[source])
    return None


def _wkt_rect(text: str) -> QgsRectangle | None:
    try:
        from qgis.core import QgsGeometry
    except ImportError:
        return None
    try:
        geometry = QgsGeometry.fromWkt(str(text))
    except Exception:  # noqa: BLE001
        return None
    if geometry is None or geometry.isNull():
        return None
    try:
        rect = geometry.boundingBox()
    except Exception:  # noqa: BLE001
        return None
    return None if rect is None or rect.isNull() else QgsRectangle(rect)




_WKT_RE = re.compile(
    r"\b(?:POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON)"
    r"\s*Z?\s*M?\s*\([^()]*(?:\([^()]*\)[^()]*)*\)",
    re.IGNORECASE)


_POINT_XY_RE = re.compile(r"QgsPointXY\s*\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")

_CRS_NAME_RE = re.compile(r"\b(?:EPSG|ESRI|IGNF|OGC|CRS):\s*\w+", re.IGNORECASE)


def _wkts(args) -> list:

    out: list = []

    def walk(value, depth: int) -> None:
        if len(out) >= 200 or depth > 4:
            return
        if isinstance(value, str):


            head = value.strip()[:14].upper()
            if head.startswith(("POINT", "LINESTRING", "POLYGON", "MULTIPOINT",
                                "MULTILINE", "MULTIPOLYGON", "GEOMETRYCOLLECTION")):
                out.append(value)
                return
            if len(value) <= 100000 and _CRS_NAME_RE.search(value):
                out.extend(_WKT_RE.findall(value)[:20])
                for x, y in _POINT_XY_RE.findall(value)[:20]:
                    out.append(f"POINT({x} {y})")
            return
        if isinstance(value, dict):
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value[:200]:
                walk(item, depth + 1)

    walk(args, 0)
    return out


def _numbers(value, count: int) -> list | None:

    if isinstance(value, str):
        parts = [p for p in value.replace(";", ",").split(",") if p.strip()]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return None
    if len(parts) != count:
        return None
    try:
        numbers = [float(p) for p in parts]
    except (TypeError, ValueError):
        return None


    return numbers if all(math.isfinite(n) for n in numbers) else None


def _antimeridian_halves(bbox: list, crs) -> list | None:













    try:
        if crs is None or not crs.isGeographic():
            return None
    except Exception:  # noqa: BLE001
        return None
    west, east = bbox[0], bbox[2]
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        return None
    if not (-90.0 <= bbox[1] <= 90.0 and -90.0 <= bbox[3] <= 90.0):
        return None
    if west - east <= 180.0:
        return None
    y0, y1 = min(bbox[1], bbox[3]), max(bbox[1], bbox[3])
    return [QgsRectangle(west, y0, 180.0, y1), QgsRectangle(-180.0, y0, east, y1)]


def _nearer_half(halves: list, crs, canvas) -> QgsRectangle | None:







    rects = [r for r in (_rect_from_crs(half, crs, canvas) for half in halves) if r is not None]
    rects = [r for r in rects if not r.isNull()]
    if not rects:
        return None
    if len(rects) == 1:
        return rects[0]
    try:
        here = canvas.extent().center().x()
    except Exception:  # noqa: BLE001
        return rects[0]
    return min(rects, key=lambda r: abs(r.center().x() - here))


def _is_origin(rect) -> bool:

    try:
        return rect.width() == 0 and rect.height() == 0 and rect.xMinimum() == 0 and rect.yMinimum() == 0
    except Exception:  # noqa: BLE001
        return False


def target_of(name: str, args, canvas=None) -> QgsRectangle | None:









    canvas = canvas if canvas is not None else _canvas()
    if canvas is None or not isinstance(args, dict):
        return None



    if name.startswith("add_layout_"):
        return None
    declared = args.get("crs") or args.get("target_crs") or args.get("output_crs")
    source = _crs(declared) if isinstance(declared, str) and ":" in declared else None
    wkts = _wkts(args)
    if wkts:
        rect = None
        for text in wkts[:50]:
            one = _wkt_rect(text)
            if one is None or _is_origin(one):
                continue
            if rect is None:
                rect = QgsRectangle(one)
            else:
                rect.combineExtentWith(one)
        if rect is not None:
            crs = source or _named_layer_crs(args)
            if crs is None:
                crs = _crs("EPSG:4326") if _looks_like_degrees(rect) else None
            if crs is None:
                return None
            return _rect_from_crs(rect, crs, canvas)
    bbox = _numbers(args.get("bbox") or args.get("extent"), 4)
    if bbox is not None:
        if all(number == 0 for number in bbox):
            return None
        rect = QgsRectangle(min(bbox[0], bbox[2]), min(bbox[1], bbox[3]),
                            max(bbox[0], bbox[2]), max(bbox[1], bbox[3]))
        crs = source or (_crs("EPSG:4326") if _looks_like_degrees(rect) else _named_layer_crs(args))
        if crs is None:
            return None
        halves = _antimeridian_halves(bbox, crs)
        if halves is not None:
            return _nearer_half(halves, crs, canvas)
        return _rect_from_crs(rect, crs, canvas)
    point = _numbers(args.get("point") or args.get("coordinates") or args.get("center"), 2)
    if point is None and args.get("lon") is not None and args.get("lat") is not None:
        point = _numbers([args.get("lon"), args.get("lat")], 2)
    if point is None and args.get("x") is not None and args.get("y") is not None:



        declared = spec(name)
        if declared is None or not declared.xy_crs:
            return None
        source = _xy_crs(declared.xy_crs, args)
        if source is None:
            return None
        point = _numbers([args.get("x"), args.get("y")], 2)
    if point is not None:
        if point[0] == 0 and point[1] == 0:
            return None
        rect = QgsRectangle(point[0], point[1], point[0], point[1])
        crs = source or (_crs("EPSG:4326") if _looks_like_degrees(rect) else _named_layer_crs(args))
        return _rect_from_crs(rect, crs, canvas) if crs is not None else None
    return None


def go_to(rect, canvas=None) -> str:









    return move_to(rect, canvas)


class HandWatch(QObject):









    def __init__(self, on_taken):
        super().__init__()
        self._on_taken = on_taken
        self._canvas = None
        self._held = False
        self._touching = False
        types = {name: enum_member(QEvent, "Type", name, None) for name in (
            "MouseButtonPress", "MouseButtonRelease", "Wheel", "KeyPress", "NativeGesture", "Gesture")}
        self._press = types.pop("MouseButtonPress")
        self._release = types.pop("MouseButtonRelease")
        self._brief = {kind for kind in types.values() if kind is not None}

    def attach(self, canvas) -> None:
        self.detach()
        if canvas is None:
            return
        try:
            canvas.viewport().installEventFilter(self)
            canvas.installEventFilter(self)
            canvas.extentsChanged.connect(self._on_extents)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Follow: cannot watch the user's hand on the map: {exc}")
            return
        self._canvas = canvas

    def detach(self) -> None:
        canvas, self._canvas = self._canvas, None
        self._held = self._touching = False
        if canvas is None:
            return
        try:
            canvas.viewport().removeEventFilter(self)
            canvas.removeEventFilter(self)
            canvas.extentsChanged.disconnect(self._on_extents)
        except (TypeError, RuntimeError):
            pass

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind == self._press:
            self._held = True
        elif kind == self._release:


            self._touching = True
            QTimer.singleShot(0, self._released)
        elif kind in self._brief:
            self._touching = True
            QTimer.singleShot(0, self._let_go)
        return False

    def _let_go(self) -> None:
        self._touching = False

    def _released(self) -> None:
        self._touching = self._held = False

    def _on_extents(self) -> None:
        if self._held or self._touching:
            self._on_taken()


class Follower:


    def __init__(self, watcher: EditWatcher | None = None):
        self.watcher = watcher if watcher is not None else EditWatcher()
        self._timer: QTimer | None = None
        self.enabled = True
        self.last_action = ""
        self.last_rect: QgsRectangle | None = None


        self._approached_at: float | None = None
        self._halted = False
        self._named: list[str] = []


        self.view_taken = False
        self._hands = HandWatch(self._user_took_view)

    def begin(self) -> None:
        self._approached_at = None
        self._halted = False
        self.view_taken = False
        self.watcher.start()
        self._hands.attach(_canvas())



        signal = getattr(QgsProject.instance(), "layersAdded", None)
        try:
            if signal is not None:
                signal.disconnect(self._on_layers_added)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layersAdded.connect(self._on_layers_added)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Follow: cannot watch the layers a run adds: {exc}")

    def _on_layers_added(self, _layers) -> None:

        keep_view_over_add()

    def end(self) -> None:
        self.watcher.stop()
        self._hands.detach()
        try:
            QgsProject.instance().layersAdded.disconnect(self._on_layers_added)
        except Exception:  # nosec B110
            pass
        self._cancel_timer()
        self._named = []
        self._approached_at = None

    def halt(self) -> None:






        self._halted = True
        self._cancel_timer()
        self.watcher.take()
        self._named = []

    def _on(self) -> bool:


        if not self.enabled or self._halted or self.view_taken:
            return False
        try:
            from .settings import Settings
            return bool(Settings().follow_edits)
        except Exception:  # noqa: BLE001
            return True

    def _user_took_view(self) -> None:
        if self.view_taken:
            return
        self.view_taken = True
        canvas_glide.stop()
        self._cancel_timer()
        self.watcher.take()
        self._named = []
        log("Follow: the user moved the map, the view is theirs until the next run")

    def approach(self, name: str, args) -> str:








        if not self._on():
            return ""
        if self._approached_at is not None and (
                self._timer is not None
                or time.monotonic() - self._approached_at < FEEL["settle_ms"] / 1000.0):


            return ""
        try:
            rect = target_of(str(name or ""), args)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Follow: no approach for {name}: {exc}")
            return ""
        if rect is None:
            return ""
        try:
            action = go_to(rect)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Follow: could not go to {name}'s place: {exc}")
            return ""
        if action in ("panned", "zoomed", "reframed"):
            self._approached_at = time.monotonic()
            self.last_rect = QgsRectangle(rect)
        return action

    def request(self, layers=()) -> None:





        if not self._on():
            self.watcher.take()
            self._named = []
            return
        for layer_id in layers or ():
            if isinstance(layer_id, str) and layer_id and layer_id not in self._named:
                self._named.append(layer_id)
        self._cancel_timer()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setInterval(int(FEEL["settle_ms"]))
        timer.timeout.connect(self.now)
        timer.start()
        self._timer = timer

    def chose_view(self) -> None:






        self._cancel_timer()
        self.watcher.take()
        self._named = []

    def now(self) -> str:








        self._cancel_timer()
        self._approached_at = None
        rect = self.watcher.take()
        named, self._named = self._named, []
        if not self._on():
            return ""
        if rect is None and named:
            rect = layers_rect(named)
        if rect is None:
            return ""
        self.last_rect = QgsRectangle(rect)
        self.last_action = move_to(rect)
        return self.last_action

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            try:
                self._timer.stop()
            except RuntimeError:
                pass
            self._timer = None
