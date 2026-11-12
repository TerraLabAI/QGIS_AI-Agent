# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QPainter, QPalette
from qgis.PyQt.QtWidgets import QMainWindow, QStyle, QStyleOption, QWidget

from ..chat_panel import ChatPanel
from ..style import LINE, PAGE, qcolor, with_platform_font

DOCK_OBJECT_NAME = "AIAgentDock"
_FOLLOWED = (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide,
             QEvent.Type.ParentChange)


class _SeparatorVeil(QWidget):










    def __init__(self, dock):
        super().__init__(dock.parentWidget())
        self._dock = dock
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()
        dock.installEventFilter(self)
        dock.topLevelChanged.connect(self.follow)
        dock.dockLocationChanged.connect(self.follow)
        dock.visibilityChanged.connect(self.follow)
        dock.destroyed.connect(self.deleteLater)

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() in _FOLLOWED:
            self.follow()
        return False

    def follow(self, *_args) -> None:

        dock = self._dock
        window = dock.parentWidget()
        if not isinstance(window, QMainWindow) or dock.isFloating() or not dock.isVisible():
            self.hide()
            return
        if self.parentWidget() is not window:
            self.setParent(window)
        extent = window.style().pixelMetric(QStyle.PixelMetric.PM_DockWidgetSeparatorExtent, None, window)
        frame = dock.geometry()
        on_right = window.dockWidgetArea(dock) == Qt.DockWidgetArea.RightDockWidgetArea
        self.setGeometry(frame.x() - extent if on_right else frame.x() + frame.width(),
                         frame.y(), extent, frame.height())
        if not self.isVisible():
            self.show()
            self.raise_()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        ground = qcolor(PAGE)
        painter.fillRect(self.rect(), ground)

        bar = self._dock.titleBarWidget()
        if bar is not None:
            painter.fillRect(0, bar.geometry().bottom(), self.width(), 1, qcolor(LINE))
        option = QStyleOption()
        option.initFrom(self)
        option.rect = self.rect()
        option.palette.setColor(QPalette.ColorRole.Window, ground)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorDockWidgetResizeHandle,
                                   option, painter, self)
        painter.end()


class AIAgentDock(QgsDockWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("AI Agent"))
        self.setObjectName(DOCK_OBJECT_NAME)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)


        with_platform_font(self)
        self.panel = ChatPanel(self)
        self.setWidget(self.panel)
        header = self.panel.detach_header()
        header.set_dock(self)
        self.setTitleBarWidget(header)
        self.panel.mascot_stage.set_host(self)
        self.setAcceptDrops(True)
        self._veil = _SeparatorVeil(self)

    def dragEnterEvent(self, event):  # noqa: N802
        self.panel.dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802
        self.panel.dragMoveEvent(event)

    def dragLeaveEvent(self, event):  # noqa: N802
        self.panel.dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        self.panel.dropEvent(event)

    def cleanup(self) -> None:

        try:
            self._veil.hide()
            self._veil.deleteLater()
        except RuntimeError:
            pass
        try:
            self.panel.cleanup()
        except (RuntimeError, AttributeError):
            pass
