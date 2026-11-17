# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QObject, QRect, Qt, QTimer
from qgis.PyQt.QtGui import QPalette
from qgis.PyQt.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QSizePolicy, QWidget

from .shared import released_on



_PASS_WHEN_DISABLED = frozenset((
    QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick,
    QEvent.Type.MouseMove, QEvent.Type.Wheel, QEvent.Type.HoverEnter, QEvent.Type.HoverLeave,
    QEvent.Type.HoverMove, QEvent.Type.ContextMenu,
))
_ACTIVATE_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)


def words_of(widget: QWidget) -> str:


    from .loader import ShimmerLabel

    words = []
    for child in widget.findChildren(QWidget):
        if not isinstance(child, (QLabel, ShimmerLabel)) or not child.isVisibleTo(widget):
            continue
        full = getattr(child, "full_text", None)
        text = " ".join((full() if callable(full) else child.text()).split())
        if text and not text.startswith("<"):
            words.append(text)
    return " ".join(words)


class PressSurface(QAbstractButton):













    def __init__(self, parent=None, focus: Qt.FocusPolicy = Qt.FocusPolicy.NoFocus):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred))
        self.setForegroundRole(QPalette.ColorRole.WindowText)
        self.setBackgroundRole(QPalette.ColorRole.Window)
        self.setFocusPolicy(focus)

    def event(self, event):  # noqa: N802
        if not self.isEnabled() and event.type() in _PASS_WHEN_DISABLED:
            return QWidget.event(self, event)
        return super().event(event)

    def paintEvent(self, event):  # noqa: N802
        return

    def mousePressEvent(self, event):  # noqa: N802
        QWidget.mousePressEvent(self, event)

    def mouseMoveEvent(self, event):  # noqa: N802
        QWidget.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        QWidget.mouseReleaseEvent(self, event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in _ACTIVATE_KEYS:
            if not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        QWidget.keyPressEvent(self, event)

    def keyReleaseEvent(self, event):  # noqa: N802
        QWidget.keyReleaseEvent(self, event)


class _ClickRow(PressSurface):


    def __init__(self, parent=None):

        super().__init__(parent, Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if released_on(self, event):
            self.click()
            return
        super().mouseReleaseEvent(event)


class LabelButton(PressSurface):




    def __init__(self, label: QLabel, parent=None, focus: Qt.FocusPolicy = Qt.FocusPolicy.NoFocus):
        super().__init__(parent, focus)
        self.label = label
        label.setParent(self)
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):  # noqa: N802

        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if released_on(self, event):
            self.click()
            return
        super().mouseReleaseEvent(event)






HIDDEN_FROM_AX = "qt_rubberband"


class RowButtons(QObject):












    def __init__(self, view, host: QWidget, on_press, visible_only: bool = False):
        super().__init__(host)
        view.setObjectName(HIDDEN_FROM_AX)
        self._view = view
        self._host = host
        self._on_press = on_press
        self._visible_only = visible_only
        self._buttons: list[PressSurface] = []
        self._labels: list[QLabel] = []
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(0)
        self._pending.timeout.connect(self.sync)
        model = view.model()
        for signal in (model.modelReset, model.rowsInserted, model.rowsRemoved,
                       model.layoutChanged, model.dataChanged, view.verticalScrollBar().valueChanged):
            signal.connect(self._queue)
        view.viewport().installEventFilter(self)

    def _queue(self, *_args) -> None:

        self._pending.start()

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Move):
            self._queue()
        return False

    def sync(self) -> None:
        view, model = self._view, self._view.model()
        viewport = view.viewport()
        wanted = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        seen: dict[str, int] = {}
        used = {"button": 0, "label": 0}
        for row in range(model.rowCount(view.rootIndex())):
            index = model.index(row, 0, view.rootIndex())
            if view.isRowHidden(row):
                continue
            rect = view.visualRect(index)
            if self._visible_only and not rect.intersects(viewport.rect()):
                continue
            label = " ".join(str(index.data(Qt.ItemDataRole.AccessibleTextRole)
                                 or index.data(Qt.ItemDataRole.DisplayRole) or "").split())
            pressable = (model.flags(index) & wanted) == wanted
            if not pressable and not label:
                continue
            seen[label] = seen.get(label, 0) + 1
            name = label if seen[label] == 1 else f"{label} ({seen[label]})"
            element = self._element("button" if pressable else "label", used)
            element.row = row
            if element.accessibleName() != name:
                element.setAccessibleName(name)
            note = str(index.data(Qt.ItemDataRole.AccessibleDescriptionRole) or "")
            if element.accessibleDescription() != note:
                element.setAccessibleDescription(note)
            element.setGeometry(QRect(viewport.mapTo(self._host, rect.topLeft()), rect.size()))
            element.raise_()
            element.show()
        for element in self._buttons[used["button"]:] + self._labels[used["label"]:]:
            element.hide()

    def _element(self, kind: str, used: dict) -> QWidget:
        pool = self._buttons if kind == "button" else self._labels
        if used[kind] == len(pool):
            if kind == "button":
                element = PressSurface(self._host)
                element.clicked.connect(lambda _checked=False, b=element: self._press(b))
            else:
                element = QLabel(self._host)
            element.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            pool.append(element)
        used[kind] += 1
        return pool[used[kind] - 1]

    def _press(self, button: PressSurface) -> None:
        index = self._view.model().index(button.row, 0, self._view.rootIndex())
        if index.isValid():
            self._on_press(index)


class _ComboRows(RowButtons):





    def __init__(self, combo):
        view = combo.view()
        super().__init__(view, view.parentWidget(),
                         lambda index: (combo.setCurrentIndex(index.row()), combo.hidePopup()))
        self._shrink = QTimer(self)
        self._shrink.setSingleShot(True)
        self._shrink.setInterval(0)
        self._shrink.timeout.connect(self._shrink_closed)
        self._host.installEventFilter(self)
        self._shrink.start()

    def _shrink_closed(self) -> None:
        if not self._host.isVisible():
            self._view.setGeometry(QRect())

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._host:
            kind = event.type()
            if kind == QEvent.Type.Show and self._host.layout() is not None:
                self._host.layout().invalidate()
                self._host.layout().activate()
            elif kind in (QEvent.Type.Hide, QEvent.Type.LayoutRequest):
                self._shrink.start()
            return False
        return super().eventFilter(watched, event)


def press_combo_rows(combo) -> RowButtons:

    return _ComboRows(combo)
