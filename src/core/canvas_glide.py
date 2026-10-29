# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import math
from contextlib import contextmanager

from qgis.core import QgsRectangle
from qgis.PyQt.QtCore import QElapsedTimer, QEvent, QObject, Qt, QTimer

from .logger import log_warning
from .qt_compat import enum_member







RHO = 1.42
SPEED = 0.9
MIN_MS = 1000
MAX_MS = 3000
FRAME_MS = 16


RENDER_WAIT_MS = 1500



OVERVIEW_WAIT_MS = 500
OVERVIEW_PIXEL_RATIO = 0.25





WAYPOINTS = (0.5, 0.75)
WAYPOINT_PIXEL_RATIO = 1.0

WAYPOINT_MIN_LENGTH = 1.5

OVERLAY_Z = 900

_active: Glide | None = None


_retired: set = set()


def _ease(t: float) -> float:

    x1, y1, x2, y2 = 0.25, 0.1, 0.25, 1.0

    def bezier(a, b, u):
        return 3 * a * (1 - u) ** 2 * u + 3 * b * (1 - u) * u * u + u ** 3

    u = t
    for _ in range(8):
        slope = 3 * x1 * (1 - u) ** 2 + 6 * (x2 - x1) * (1 - u) * u + 3 * (1 - x2) * u * u
        if abs(slope) < 1e-6:
            break
        u = min(1.0, max(0.0, u - (bezier(x1, x2, u) - t) / slope))
    return bezier(y1, y2, u)


class Path:


    def __init__(self, a: QgsRectangle, b: QgsRectangle):


        self.ratio = a.height() / a.width()
        self.side = max(1.0, self.ratio)
        self.x0, self.y0 = a.center().x(), a.center().y()
        self.dx, self.dy = b.center().x() - self.x0, b.center().y() - self.y0
        self.w0 = a.width() * self.side
        w1 = b.width() * self.side
        d = math.hypot(self.dx, self.dy)
        rho2 = RHO * RHO
        if d < self.w0 * 1e-6:
            self.d = 0.0
            self.length = abs(math.log(w1 / self.w0)) / RHO
            self.sign = 1.0 if w1 >= self.w0 else -1.0
            return
        self.d = d

        def r(i):
            w = w1 if i else self.w0
            b_ = (w1 * w1 - self.w0 * self.w0 + (-1 if i else 1) * rho2 * rho2 * d * d) / (2 * w * rho2 * d)
            return math.log(math.sqrt(b_ * b_ + 1) - b_)

        self.r0 = r(0)
        self.length = (r(1) - self.r0) / RHO

    def at(self, k: float) -> QgsRectangle:
        s = k * self.length
        if self.d == 0.0:
            u, w = 0.0, self.w0 * math.exp(self.sign * RHO * s)
        else:
            rho2 = RHO * RHO
            cosh0 = math.cosh(self.r0)
            u = self.w0 / (rho2 * self.d) * (cosh0 * math.tanh(RHO * s + self.r0) - math.sinh(self.r0))
            w = self.w0 * cosh0 / math.cosh(RHO * s + self.r0)
        x, y = self.x0 + u * self.dx, self.y0 + u * self.dy
        width = w / self.side
        height = width * self.ratio
        return QgsRectangle(x - width / 2, y - height / 2, x + width / 2, y + height / 2)


class Picture:


    def __init__(self, canvas):
        viewport = canvas.viewport()


        self.pixmap = viewport.grab()
        self.extent = QgsRectangle(canvas.extent())
        self.size = (viewport.width(), viewport.height())
        self.crs = canvas.mapSettings().destinationCrs().authid()
        self.rotation = canvas.rotation()

    @classmethod
    def of(cls, pixmap, extent: QgsRectangle, size, crs: str) -> Picture:
        picture = cls.__new__(cls)
        picture.pixmap, picture.extent, picture.size, picture.crs, picture.rotation = pixmap, extent, size, crs, 0.0
        return picture


def take(canvas) -> Picture | None:

    try:
        picture = Picture(canvas)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Canvas glide: no picture: {exc}")
        return None
    if picture.pixmap.isNull() or picture.extent.isEmpty() or min(picture.size) <= 0:
        return None
    return picture


def start(canvas, picture: Picture | None) -> None:









    global _active
    if picture is None:
        stop()
        return
    try:
        if canvas.mapSettings().destinationCrs().authid() != picture.crs or picture.rotation or canvas.rotation():
            stop()
            return
        if _active is not None and _active.canvas is canvas and _active.go_on():
            return
        stop()
        glide = Glide(canvas, picture)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Canvas glide not started: {exc}")
        return
    _active = glide


@contextmanager
def gliding(canvas):

    picture = take(canvas) if canvas is not None else None
    yield
    if picture is not None and not same_extent(picture.extent, canvas.extent()):
        start(canvas, picture)


def same_extent(a: QgsRectangle, b: QgsRectangle) -> bool:
    return abs(a.width() - b.width()) <= a.width() * 1e-6 and a.center().distance(b.center()) <= a.width() * 1e-6


def stop() -> None:

    global _active
    glide, _active = _active, None
    if glide is not None:
        glide.finish()


class Glide:
    def __init__(self, canvas, picture: Picture, drawn: bool = False):
        from qgis.PyQt.QtGui import QBrush, QPen
        from qgis.PyQt.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem

        self.canvas = canvas
        self.before = picture.extent
        self.after = QgsRectangle(canvas.extent())
        self.width, self.height = picture.size
        self.crs = picture.crs
        self.path = Path(self.before, self.after)
        self.ms = min(MAX_MS, max(MIN_MS, 1000 * self.path.length / SPEED))
        self.pending = False

        self._pixmap_item = QGraphicsPixmapItem
        origin = canvas.mapToScene(0, 0)
        self.origin = (origin.x(), origin.y())
        self.ground = QGraphicsRectItem(0, 0, self.width, self.height)
        self.ground.setBrush(QBrush(canvas.canvasColor()))
        self.ground.setPen(QPen(Qt.PenStyle.NoPen))
        self.ground.setZValue(OVERLAY_Z)
        self.old = _smooth(QGraphicsPixmapItem(picture.pixmap), self._z(self.before, picture.pixmap.devicePixelRatio()))
        self.new = None


        self.under: list[dict] = []
        self.job = None
        scene = canvas.scene()
        for item in (self.ground, self.old):
            scene.addItem(item)
            item.setPos(*self.origin)
        self.drawn = drawn
        self.drawn_at = _started() if drawn else None
        self._draw_under()

        self.hands = _Hands()
        for widget in (canvas, canvas.viewport()):
            widget.installEventFilter(self.hands)
        self.clock: QElapsedTimer | None = None
        self.waited = _started()
        canvas.mapCanvasRefreshed.connect(self._on_rendered)
        self.timer = QTimer()
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self._frame)
        self.timer.start()

    def go_on(self) -> bool:

        if self.clock is not None:
            self.pending = True
            return True
        self._drop_under()
        self.after = QgsRectangle(self.canvas.extent())
        self.path = Path(self.before, self.after)
        self.ms = min(MAX_MS, max(MIN_MS, 1000 * self.path.length / SPEED))
        self.drawn, self.drawn_at = False, None
        self._draw_under()
        return True

    def _drop_under(self) -> None:
        under, self.under, self.job = self.under, [], None
        for layer in under:
            job = layer["job"]
            if job is not None:
                try:
                    job.finished.disconnect()
                    if job.isActive():
                        _retired.add(job)
                        job.finished.connect(lambda done=job: _retired.discard(done))
                        job.cancelWithoutBlocking()
                except (TypeError, RuntimeError):
                    pass
            item = layer["item"]
            if item is not None and item.scene() is not None:
                item.scene().removeItem(item)

    def _draw_under(self) -> None:













        span = QgsRectangle(self.before)
        for i in range(21):
            span.combineExtentWith(self.path.at(i / 20))
        if span.width() > self.after.width() * 1.001 or span.height() > self.after.height() * 1.001:
            self.job = self._render(span, OVERVIEW_PIXEL_RATIO)
        if self.job is None:
            self._draw_waypoints()

    def _draw_waypoints(self) -> None:
        if self.path.length < WAYPOINT_MIN_LENGTH:
            return



        highest = max((self.path.at(i / 40) for i in range(41)), key=lambda view: view.width())
        for view in (highest, *(self.path.at(_ease(t)) for t in WAYPOINTS)):
            self._render(view, WAYPOINT_PIXEL_RATIO)

    def _z(self, extent: QgsRectangle, pixel_ratio: float) -> float:

        ground_per_pixel = extent.width() / (self.width * max(pixel_ratio, 0.01))
        return OVERLAY_Z + 2 - 0.01 * math.log2(ground_per_pixel)

    def _render(self, extent: QgsRectangle, pixel_ratio: float):
        from functools import partial

        from qgis.core import QgsMapRendererParallelJob, QgsMapSettings
        from qgis.PyQt.QtCore import QSize

        settings = QgsMapSettings(self.canvas.mapSettings())
        settings.setOutputSize(QSize(self.width, self.height))
        settings.setDevicePixelRatio(pixel_ratio)
        settings.setExtent(extent)
        visible = QgsRectangle(settings.visibleExtent())
        layer = {"job": None, "extent": visible, "z": self._z(visible, pixel_ratio), "item": None}
        job = QgsMapRendererParallelJob(settings)
        layer["job"] = job
        self.under.append(layer)
        job.finished.connect(partial(self._on_under, layer))
        job.start()
        return job

    def _on_under(self, layer: dict) -> None:
        from qgis.PyQt.QtGui import QPixmap

        job, layer["job"] = layer["job"], None
        if job is None or layer not in self.under:
            return
        gate = job is self.job
        if gate:
            self.job = None
        image = job.renderedImage()
        if not image.isNull() and image.width() > 0:
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(image.width() / self.width)
            layer["item"] = item = _smooth(self._pixmap_item(pixmap), layer["z"])
            self.canvas.scene().addItem(item)
            if self.clock is None:
                item.setVisible(False)
            else:
                self._frame()
        if gate:
            self._draw_waypoints()
            self._maybe_depart()

    def _on_rendered(self) -> None:
        self.drawn, self.drawn_at = True, _started()
        if self.clock is None:
            self._maybe_depart()
            return


        try:
            self.canvas.mapCanvasRefreshed.disconnect(self._on_rendered)
        except (TypeError, RuntimeError):
            pass
        pixmap = self._grab_canvas()
        self.new.setPixmap(pixmap)
        self.new.setZValue(self._z(self.after, pixmap.devicePixelRatio()) + 1e-6)
        self._frame()

    def _grab_canvas(self):

        hidden = [item for item in (self.ground, self.old, self.new, *(layer["item"] for layer in self.under))
                  if item is not None and item.isVisible()]
        for item in hidden:
            item.setVisible(False)
        pixmap = self.canvas.viewport().grab()
        for item in hidden:
            item.setVisible(True)
        return pixmap

    def _maybe_depart(self) -> None:
        if _active is self and self.clock is None and self.drawn and self.job is None:
            self._depart()

    def _depart(self) -> None:

        if self.drawn:
            try:
                self.canvas.mapCanvasRefreshed.disconnect(self._on_rendered)
            except (TypeError, RuntimeError):
                pass
        for layer in self.under:
            if layer["item"] is not None:
                layer["item"].setVisible(True)
        pixmap = self._grab_canvas()


        z = self._z(self.after, pixmap.devicePixelRatio()) + 1e-6 if self.drawn else OVERLAY_Z + 0.1
        self.new = _smooth(self._pixmap_item(pixmap), z)
        self.canvas.scene().addItem(self.new)
        self.clock = _started()
        self._frame()

    def _place(self, item, extent: QgsRectangle, view: QgsRectangle) -> None:

        item.setScale(extent.width() / view.width())
        item.setPos(self.origin[0] + (extent.xMinimum() - view.xMinimum()) / view.width() * self.width,
                    self.origin[1] + (view.yMaximum() - extent.yMaximum()) / view.height() * self.height)

    def _frame(self) -> None:
        try:
            viewport = self.canvas.viewport()
            if (viewport.width(), viewport.height()) != (self.width, self.height):
                return stop()
            if not self.pending and not same_extent(self.after, self.canvas.extent()):

                return stop()
            if self.clock is None:
                if self.waited.elapsed() >= RENDER_WAIT_MS or (
                        self.drawn_at is not None and self.drawn_at.elapsed() >= OVERVIEW_WAIT_MS):
                    self._depart()
                return None
            t = min(1.0, self.clock.elapsed() / self.ms)
            view = self.path.at(_ease(t))
            for layer in self.under:
                if layer["item"] is not None:
                    self._place(layer["item"], layer["extent"], view)
            self._place(self.old, self.before, view)
            self._place(self.new, self.after, view)
            if t >= 1.0:
                self._land()
        except RuntimeError:
            stop()

    def _land(self) -> None:

        canvas = self.canvas
        if not self.pending or same_extent(self.after, canvas.extent()) \
                or canvas.mapSettings().destinationCrs().authid() != self.crs or canvas.rotation():
            stop()
            return
        picture = Picture.of(self.new.pixmap(), self.after, (self.width, self.height), self.crs)
        stop()
        global _active
        try:
            _active = Glide(canvas, picture, drawn=not canvas.isDrawing())
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Canvas glide not chained: {exc}")
            return
        if _active.drawn:
            QTimer.singleShot(0, _active._maybe_depart)

    def finish(self) -> None:
        try:
            self.timer.stop()
        except RuntimeError:
            pass
        for widget in (self.canvas, self.canvas.viewport()):
            try:
                widget.removeEventFilter(self.hands)
            except RuntimeError:
                pass
        try:
            self.canvas.mapCanvasRefreshed.disconnect(self._on_rendered)
        except (TypeError, RuntimeError):
            pass
        self._drop_under()
        for item in (self.new, self.old, self.ground):
            if item is None:
                continue
            try:
                scene = item.scene()
                if scene is not None:
                    scene.removeItem(item)
            except RuntimeError:
                pass


class _Hands(QObject):


    def __init__(self):
        super().__init__()
        kinds = (enum_member(QEvent, "Type", name, None) for name in (
            "MouseButtonPress", "Wheel", "KeyPress", "NativeGesture", "Gesture", "TouchBegin"))
        self._touch = {kind for kind in kinds if kind is not None}

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in self._touch:
            stop()
        return False


def _smooth(item, z: float):
    item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
    item.setZValue(z)
    return item


def _started() -> QElapsedTimer:
    clock = QElapsedTimer()
    clock.start()
    return clock
