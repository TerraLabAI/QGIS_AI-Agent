# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The map goes where the agent works, so a run is something you watch happen."""




































from __future__ import annotations

import math
import re
from contextlib import contextmanager

from qgis.core import (
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QTimer

from . import ground, tuning
from .layer_order import is_backdrop
from .logger import log_warning
from .qt_compat import enum_member
from .vsi import streamed_in_place



PAD_SHARE = 0.25


PAN_SHARE = 0.6


CONTAINED_SHARE = 0.1


POINT_SHARE = 0.08



APPROACH_SPAN_M = 600



APPROACH_SPAN_DEG = 0.0054



APPROACH_KEEP_RATIO = 400.0

SETTLE_MS = 180



_MAX_PER_SIGNAL = 2000

FLASH_MS = 900







_SHIPPED_FEEL = {
    "pad_share": PAD_SHARE, "pan_share": PAN_SHARE, "contained_share": CONTAINED_SHARE,
    "point_share": POINT_SHARE, "approach_span_m": float(APPROACH_SPAN_M),
    "approach_keep_ratio": APPROACH_KEEP_RATIO, "settle_ms": SETTLE_MS, "flash_ms": FLASH_MS,
}
FEEL: dict = dict(_SHIPPED_FEEL)


def _refresh_feel() -> None:
    """Rebuild ``FEEL`` from the policy in force. Rebound whole, never edited."""
    global FEEL
    FEEL = {name: tuning.number("follow", name, shipped) for name, shipped in _SHIPPED_FEEL.items()}


tuning.subscribe(_refresh_feel)


def _iface():
    """QGIS's own interface, or None outside QGIS (tests, headless)."""
    try:
        from qgis.utils import iface
    except ImportError:
        return None
    return iface


def _canvas():
    iface = _iface()
    if iface is None:
        return None
    try:
        return iface.mapCanvas()
    except Exception:  # noqa: BLE001 - a canvas is not guaranteed
        return None


def _to_canvas_crs(rect, layer, canvas) -> QgsRectangle | None:
    """A layer's rectangle in the canvas CRS, or None when it cannot get there."""










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
    except Exception as exc:  # noqa: BLE001 - an unbuildable transform is not an error here
        log_warning(f"Follow: no transform from {source.authid()} to {target.authid()}: {exc}")
        return None


def _deleted(obj) -> bool:
    """True when Qt has already destroyed the C++ object behind ``obj``."""




    try:
        import sip
    except ImportError:
        try:
            from qgis.PyQt import sip
        except ImportError:
            return False
    try:
        return bool(sip.isdeleted(obj))
    except (TypeError, ValueError, RuntimeError):
        return False


def _grown(rect: QgsRectangle, share: float) -> QgsRectangle:
    """The rectangle with air around it, sized on its longest side."""
    pad = max(rect.width(), rect.height()) * share
    out = QgsRectangle(rect)
    out.grow(pad if pad > 0 else 0.0)
    return out


class EditWatcher:
    """Where this run has touched the map, gathered from the layers themselves."""








    def __init__(self):
        self._rect: QgsRectangle | None = None
        self._layers: list = []
        self._project = None
        self._watching = False



    def start(self) -> None:
        """Listen to every vector layer in the project, and to new ones."""
        self.stop()
        self._rect = None
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
            except Exception as exc:  # noqa: BLE001 - an old build without the signal still watches
                log_warning(f"Follow: cannot watch removals: {exc}")
        for layer in list(project.mapLayers().values()):
            self._watch(layer)
        self._watching = True

    def stop(self) -> None:
        """Let go of every layer. Safe to call twice, and on a closed project."""
        if self._project is not None:
            for signal_name, slot in (("layersAdded", self._on_layers_added),
                                      ("layersWillBeRemoved", self._on_layers_removed)):
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

    def _on_layers_removed(self, layer_ids) -> None:
        """Let go of a layer the run is deleting, while it is still there."""
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

    @property
    def watching(self) -> bool:
        return self._watching

    def take(self) -> QgsRectangle | None:
        """The rectangle gathered so far, and a clean slate after it."""
        rect, self._rect = self._rect, None
        return rect

    def peek(self) -> QgsRectangle | None:
        return QgsRectangle(self._rect) if self._rect is not None else None



    def _connections(self, layer):
        """The signals of one layer, each bound to the layer that owns it."""







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
            except Exception as exc:  # noqa: BLE001 - an old build may lack one signal
                log_warning(f"Follow: cannot watch {layer.name()}: {exc}")
        if bound:
            self._layers.append((layer, bound))

    def _on_layers_added(self, layers) -> None:
        """A layer the run created: its own extent is what the run produced."""








        canvas = _canvas()
        for layer in layers or []:
            self._watch(layer)
            if canvas is None or is_backdrop(layer) or streamed_in_place(layer):
                continue
            try:
                extent = layer.extent()
            except Exception:  # noqa: BLE001  # nosec B112 - a layer that cannot state its extent is skipped
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
            except Exception:  # noqa: BLE001 - the edit buffer may not hold it yet
                return
            self._add_geometry(geometry, layer)
        return on_added

    def _committed_features_slot(self, layer):
        def on_committed(_layer_id, features):
            for feature in list(features or [])[:_MAX_PER_SIGNAL]:
                try:
                    self._add_geometry(feature.geometry(), layer)
                except Exception:  # noqa: BLE001  # nosec B112 - one bad geometry never stops the batch
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
        if self._rect is None:
            self._rect = QgsRectangle(rect)
        else:
            self._rect.combineExtentWith(rect)


def same_view(first, second) -> bool:
    """Whether two rectangles are the same view, to a pixel's worth of rounding."""
    if first is None or second is None:
        return False
    try:
        tolerance = max(second.width(), second.height()) * 1e-6
        return (abs(first.xMinimum() - second.xMinimum()) <= tolerance
                and abs(first.yMinimum() - second.yMinimum()) <= tolerance
                and abs(first.xMaximum() - second.xMaximum()) <= tolerance
                and abs(first.yMaximum() - second.yMaximum()) <= tolerance)
    except Exception:  # noqa: BLE001 - a rectangle that cannot be compared is not the same view
        return False


def _restore(canvas, rect, crs_before) -> None:
    """Put ``rect``, read under ``crs_before``, back on the canvas."""







    if canvas is None or rect is None:
        return
    target = QgsRectangle(rect)
    try:
        crs_now = canvas.mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001 - no map settings, no transform to make
        crs_now = None
    if (crs_before is not None and crs_now is not None and crs_before.isValid()
            and crs_now.isValid() and crs_before != crs_now):
        try:
            transform = QgsCoordinateTransform(crs_before, crs_now, QgsProject.instance())
            target = transform.transformBoundingBox(target)
        except Exception as exc:  # noqa: BLE001 - an unbuildable transform keeps the view as read
            log_warning(f"Follow: cannot restore the view across CRS: {exc}")
            return
    try:
        if same_view(canvas.extent(), target):
            return
        canvas.setExtent(target)
        canvas.refresh()
    except Exception as exc:  # noqa: BLE001 - a canvas that refuses the view is not an error here
        log_warning(f"Follow: view not restored: {exc}")


@contextmanager
def view_kept(canvas=None):
    """Whatever happens inside, the user is still looking at what they were."""




















    canvas = canvas if canvas is not None else _canvas()
    before = None
    crs_before = None
    was_empty = False
    if canvas is not None:
        try:
            before = QgsRectangle(canvas.extent())
            crs_before = canvas.mapSettings().destinationCrs()
        except Exception:  # noqa: BLE001 - a canvas we cannot read is a canvas we cannot restore
            before = None
        try:
            was_empty = QgsProject.instance().count() == 0
        except Exception:  # noqa: BLE001
            was_empty = False
    if before is not None and (before.isNull() or before.isEmpty()):
        before = None
    try:
        yield
    finally:
        if before is not None:
            _restore(canvas, before, crs_before)
        if canvas is not None and was_empty:
            _undo_first_layer_frame(canvas)



FIRST_FRAME_GUARD_MS = 1500


FRAME_SHARE = 0.8


def _is_framing(now: QgsRectangle, full: QgsRectangle) -> bool:
    if full.isNull() or full.isEmpty() or not now.contains(full):
        return False
    return full.width() >= now.width() * FRAME_SHARE or full.height() >= now.height() * FRAME_SHARE


def _undo_first_layer_frame(canvas) -> None:
    """Undo the bridge's framing of the first layer when a view was set after the add."""








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
    """Hold the current view against the framing that follows a layer add."""
    canvas = canvas or _canvas()
    if canvas is None:
        return
    if QgsProject.instance().count() == 0:
        return
    try:
        kept = QgsRectangle(canvas.extent())
        crs = canvas.mapSettings().destinationCrs()
    except Exception as exc:  # noqa: BLE001 - no canvas, nothing to keep
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
    """Take the canvas to ``rect``."""





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
    pan_share = FEEL["pan_share"]
    if padded.width() <= current.width() * pan_share and padded.height() <= current.height() * pan_share:

        half_w, half_h = current.width() / 2, current.height() / 2
        cx, cy = target.center().x(), target.center().y()
        canvas.setExtent(QgsRectangle(cx - half_w, cy - half_h, cx + half_w, cy + half_h))
        canvas.refresh()
        return "panned"
    canvas.setExtent(padded)
    canvas.refresh()
    return "zoomed"


def layers_rect(layer_ids, canvas=None) -> QgsRectangle | None:
    """The canvas-CRS rectangle around these layers, or None when there is none."""














    canvas = canvas if canvas is not None else _canvas()
    if canvas is None:
        return None
    project = QgsProject.instance()
    if project is None:
        return None
    try:
        visible = {layer.id() for layer in canvas.layers()}
    except Exception:  # noqa: BLE001 - a canvas that cannot list its layers hides nothing
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
    except Exception:  # noqa: BLE001 - a canvas without map settings has no CRS to give
        return None


def _approach_span(canvas) -> float:
    """The width of the box a bare point gets, in the canvas's own units."""
    crs = _canvas_crs(canvas)
    try:
        geographic = crs is not None and crs.isGeographic()
    except Exception:  # noqa: BLE001
        geographic = False
    span_m = FEEL["approach_span_m"]

    if geographic:
        return APPROACH_SPAN_DEG * span_m / APPROACH_SPAN_M





    scale = ground.canvas_metres_per_unit(canvas)
    return span_m / scale if ground.distorted(scale) else span_m


def _rect_from_crs(rect, source, canvas) -> QgsRectangle | None:
    """``rect`` read in ``source`` CRS, expressed in the canvas CRS."""
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
    except Exception as exc:  # noqa: BLE001 - an unbuildable transform moves nothing
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
    """The layer's extent in the canvas CRS, or None when it has none we can use."""
    try:
        rect = _to_canvas_crs(layer.extent(), layer, canvas)
    except Exception:  # noqa: BLE001 - a layer whose extent we cannot read is skipped
        return None
    if rect is None or rect.isNull() or rect.isEmpty():
        return None
    return rect


def _crs_of(layer):
    """The layer's CRS when it has a valid one, else None."""
    try:
        crs = layer.crs()
    except Exception:  # noqa: BLE001 - a layer that cannot answer names no CRS
        return None
    return crs if crs is not None and crs.isValid() else None


def _named_layer_crs(args: dict):
    """The CRS of the layer the call names, when the project holds it."""




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


def _wkts(args) -> list:
    """Every geometry an argument tree carries, in text, code included."""
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
            if len(value) <= 100000:
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
    """``value`` as ``count`` numbers, from a list or a comma-separated string."""
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
    """The two sides of a bbox written west-first across 180°, or None."""












    try:
        if crs is None or not crs.isGeographic():
            return None
    except Exception:  # noqa: BLE001 - a CRS that will not say is not one we wrap
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
    """Of the two sides of a wrapped extent, the one the view is already nearer."""






    rects = [r for r in (_rect_from_crs(half, crs, canvas) for half in halves) if r is not None]
    rects = [r for r in rects if not r.isNull()]
    if not rects:
        return None
    if len(rects) == 1:
        return rects[0]
    try:
        here = canvas.extent().center().x()
    except Exception:  # noqa: BLE001 - a canvas without an extent gets the first side
        return rects[0]
    return min(rects, key=lambda r: abs(r.center().x() - here))


def target_of(name: str, args, canvas=None) -> QgsRectangle | None:
    """Where a call is about to work, in the canvas CRS, or None."""






    canvas = canvas if canvas is not None else _canvas()
    if canvas is None or not isinstance(args, dict):
        return None
    declared = args.get("crs") or args.get("target_crs") or args.get("output_crs")
    source = _crs(declared) if isinstance(declared, str) and ":" in declared else None
    wkts = _wkts(args)
    if wkts:
        rect = None
        for text in wkts[:50]:
            one = _wkt_rect(text)
            if one is None:
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
        point = _numbers([args.get("x"), args.get("y")], 2)
    if point is not None:
        rect = QgsRectangle(point[0], point[1], point[0], point[1])
        crs = source or (_crs("EPSG:4326") if _looks_like_degrees(rect) else _named_layer_crs(args))
        return _rect_from_crs(rect, crs, canvas) if crs is not None else None
    return None


def go_to(rect, canvas=None) -> str:
    """Take the canvas to where a call is about to work, before it works there."""










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
    span = _approach_span(canvas)
    if target.width() < span and target.height() < span:


        centre = target.center()
        half = span / 2
        target = QgsRectangle(centre.x() - half, centre.y() - half,
                              centre.x() + half, centre.y() + half)
    padded = _grown(target, FEEL["pad_share"])
    if current.contains(padded) and _area(current) <= _area(padded) * FEEL["approach_keep_ratio"]:
        return "kept"
    canvas.setExtent(padded)
    canvas.refresh()
    return "zoomed"


class Follower:
    """One move for a whole batch of tools, and the flash that goes with it."""

    def __init__(self, watcher: EditWatcher | None = None):
        self.watcher = watcher if watcher is not None else EditWatcher()
        self._timer: QTimer | None = None
        self._band = None
        self._flash: QTimer | None = None
        self.enabled = True
        self.last_action = ""
        self.last_rect: QgsRectangle | None = None

        self._named: list[str] = []

    def begin(self) -> None:
        self.watcher.start()



        signal = getattr(QgsProject.instance(), "layersAdded", None)
        try:
            if signal is not None:
                signal.disconnect(self._on_layers_added)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layersAdded.connect(self._on_layers_added)
        except Exception as exc:  # noqa: BLE001 - the run goes on without the guard
            log_warning(f"Follow: cannot watch the layers a run adds: {exc}")

    def _on_layers_added(self, _layers) -> None:
        """A layer registered mid-run: keep the view the user is looking at."""
        keep_view_over_add()

    def end(self) -> None:
        self.watcher.stop()
        try:
            QgsProject.instance().layersAdded.disconnect(self._on_layers_added)
        except Exception:  # nosec B110 - a project without the signal has nothing to let go of
            pass
        self._cancel_timer()
        self._named = []

    def approach(self, name: str, args) -> str:
        """Go to where the call is about to work, before it runs."""





        if not self.enabled:
            return ""
        try:
            rect = target_of(str(name or ""), args)
        except Exception as exc:  # noqa: BLE001 - an approach never fails a call
            log_warning(f"Follow: no approach for {name}: {exc}")
            return ""
        if rect is None:
            return ""
        try:
            action = go_to(rect)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Follow: could not go to {name}'s place: {exc}")
            return ""
        if action == "zoomed":
            self.last_rect = QgsRectangle(rect)
            self._flash_rect(rect)
        return action

    def request(self, layers=()) -> None:
        """A modifying tool answered: move once the batch has settled."""




        if not self.enabled:
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

    def now(self) -> str:
        """Move immediately to whatever the watcher has gathered."""







        self._cancel_timer()
        rect = self.watcher.take()
        named, self._named = self._named, []
        if not self.enabled:
            return ""
        if rect is None and named:
            rect = layers_rect(named)
        if rect is None:
            return ""
        self.last_rect = QgsRectangle(rect)
        self.last_action = move_to(rect)
        if self.last_action in ("panned", "zoomed"):
            self._flash_rect(rect)
        return self.last_action

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            try:
                self._timer.stop()
            except RuntimeError:
                pass
            self._timer = None



    def _flash_rect(self, rect) -> None:
        """Draw the rectangle on the map for a moment, then take it off."""
        canvas = _canvas()
        if canvas is None:
            return
        try:
            from qgis.core import QgsGeometry
            from qgis.gui import QgsRubberBand
            from qgis.PyQt.QtCore import Qt
            from qgis.PyQt.QtGui import QColor
        except ImportError:
            return
        self._clear_flash()
        try:
            band = QgsRubberBand(canvas, QgsWkbTypes_polygon())
            band.setColor(QColor(139, 172, 39, 60))
            band.setStrokeColor(QColor(139, 172, 39, 220))
            band.setWidth(2)
            band.setLineStyle(Qt.PenStyle.SolidLine)
            band.setToGeometry(QgsGeometry.fromRect(QgsRectangle(rect)), None)
        except Exception as exc:  # noqa: BLE001 - the flash is never worth an error
            log_warning(f"Follow: no flash on this build: {exc}")
            return
        self._band = band
        flash = QTimer()
        flash.setSingleShot(True)
        flash.setInterval(int(FEEL["flash_ms"]))
        flash.timeout.connect(self._clear_flash)
        flash.start()
        self._flash = flash

    def _clear_flash(self) -> None:
        if self._flash is not None:
            try:
                self._flash.stop()
            except RuntimeError:
                pass
            self._flash = None
        band, self._band = self._band, None
        if band is None:
            return
        try:
            band.reset()
            canvas = _canvas()
            scene = canvas.scene() if canvas is not None else None
            if scene is not None:
                scene.removeItem(band)
        except Exception:  # noqa: BLE001  # nosec B110 - the band is going away either way
            pass


def QgsWkbTypes_polygon():  # noqa: N802 - it names the Qt enum it stands in for
    """The polygon geometry type, under either of the two QGIS spellings."""
    from qgis.core import QgsWkbTypes

    geometry = getattr(QgsWkbTypes, "GeometryType", None)
    value = getattr(geometry, "Polygon", None) if geometry is not None else None
    return value if value is not None else enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
