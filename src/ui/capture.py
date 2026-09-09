# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Capture a region of the map canvas or of the QGIS window as an image."""









from __future__ import annotations

from qgis.PyQt.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from qgis.PyQt.QtWidgets import QApplication, QMainWindow, QWidget

from .shared import event_pos
from .styles import BRAND_BLUE


MIN_SIDE = 8


def find_map_canvas() -> QWidget | None:
    """The map canvas the user is working in, found without ``iface``."""







    try:
        from qgis.gui import QgsMapCanvas
    except ImportError:
        return None
    best = None
    best_score = None
    for top in QApplication.topLevelWidgets():
        active = bool(top.isActiveWindow())
        for canvas in top.findChildren(QgsMapCanvas):
            if not canvas.isVisible() or canvas.width() <= 50 or canvas.height() <= 50:
                continue
            score = (1 if active else 0, canvas.width() * canvas.height())
            if best_score is None or score > best_score:
                best, best_score = canvas, score
    return best


def find_main_window() -> QWidget | None:
    """The QGIS main window: the largest visible QMainWindow."""
    best = None
    for top in QApplication.topLevelWidgets():
        if isinstance(top, QMainWindow) and top.isVisible():
            if best is None or top.width() * top.height() > best.width() * best.height():
                best = top
    return best


class RegionCapture(QWidget):
    """Drag a rectangle over ``target``; emits the pixels as a QPixmap."""

    captured = pyqtSignal(QPixmap)
    cancelled = pyqtSignal()

    def __init__(self, target: QWidget, hint: str = ""):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self._target = target
        self._hint = hint
        self._origin: QPoint | None = None
        self._rect = QRect()
        self._done = False
        self._geometry = QRect()





        self._watch = QTimer(self)
        self._watch.setInterval(80)
        self._watch.timeout.connect(self._follow_target)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def start(self) -> None:
        self._sync_geometry()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._watch.start()

    def _sync_geometry(self) -> None:
        origin = self._target.mapToGlobal(QPoint(0, 0))
        self._geometry = QRect(origin, self._target.size())
        self.setGeometry(self._geometry)

    def _follow_target(self) -> None:
        """Keep the overlay over its target, or give up out loud."""








        if self._done:
            return
        try:
            visible = self._target.isVisible()
            origin = self._target.mapToGlobal(QPoint(0, 0))
            size = self._target.size()
        except RuntimeError:
            self._cancel()
            return
        if not visible:
            self._cancel()
            return
        if size != self._geometry.size():
            drawing = self._origin is not None or not self._rect.isEmpty()
            if drawing:
                self._cancel()
                return
            self._sync_geometry()
            return
        if origin != self._geometry.topLeft():
            self._sync_geometry()



    def mousePressEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event_pos(event)
            self._rect = QRect(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt override
        if self._origin is not None:
            self._rect = QRect(self._origin, event_pos(event)).normalized()
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        rect = QRect(self._origin, event_pos(event)).normalized()
        self._origin = None
        if rect.width() < MIN_SIDE or rect.height() < MIN_SIDE:
            self._rect = QRect()
            self.update()
            return
        self._finish(rect)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)

    def _cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self._watch.stop()
        self.hide()
        self.cancelled.emit()
        self.close()

    def _finish(self, rect: QRect) -> None:
        if self._done:
            return
        self._done = True
        self._watch.stop()
        self.hide()









        self._pending_rect = QRect(rect)
        QTimer.singleShot(60, self._grab_pending)

    def _grab_pending(self) -> None:
        rect, self._pending_rect = self._pending_rect, None
        if rect is not None:
            self._grab(rect)

    def _grab(self, rect: QRect) -> None:
        try:




            if self._target.size() != self._geometry.size():
                self.cancelled.emit()
                self.close()
                return
            pixmap = self._target.grab(rect.intersected(self._target.rect()))
        except RuntimeError:
            pixmap = QPixmap()
        if pixmap.isNull():
            self.cancelled.emit()
        else:
            self.captured.emit(pixmap)
        self.close()



    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = None
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            dim = QColor(0, 0, 0, 90)
            if self._rect.isValid() and not self._rect.isEmpty():





                painter.setClipRect(self.rect())
                painter.fillRect(self.rect(), dim)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.fillRect(self._rect, Qt.GlobalColor.transparent)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                pen = QPen(QColor(BRAND_BLUE))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(self._rect)
            else:
                painter.fillRect(self.rect(), dim)
            if self._hint:
                font = QFont(self.font())
                font.setPixelSize(13)
                font.setBold(True)
                painter.setFont(font)
                text_rect = QRect(0, 12, self.width(), 24)
                painter.setPen(QColor(0, 0, 0, 160))
                painter.drawText(text_rect.translated(1, 1), Qt.AlignmentFlag.AlignHCenter, self._hint)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter, self._hint)
        except Exception:  # noqa: BLE001 - paint must never raise
            return
        finally:



            if painter is not None and painter.isActive():
                painter.end()
