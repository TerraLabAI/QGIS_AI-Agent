# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

import importlib
import os
import sys

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QEasingCurve, QPoint, Qt, QVariantAnimation
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtWidgets import QDialog, QDockWidget, QMenu

from ..core.host_platform import prefers_reduced_motion
from ..core.i18n import day_pattern, words_locale
from ..core.policy import PLUGIN_CACHE_DIR
from ..core.served_state import SITE_URL, TUTORIAL_URL, get_served_learn_items

PRODUCT_NAME = "AI Agent"


CHAT_PANEL_OBJECT = "chatPanel"
TOGGLE_DOCK_OBJECT = "aiAgentToggleDock"
TURN_DIVIDER_OBJECT = "turnDivider"



AGENT_ACTION = "agentAction"
AGENT_REQUEST_ID = "agentRequestId"
AGENT_REQUEST_IDS = "agentRequestIds"
AGENT_CARD_KIND = "agentCardKind"




TR_CONTEXT = "AIAgent"

TOGGLE_PANEL_KEYS = "Ctrl+Shift+K"


STEP_BACK_KEYS = "Ctrl+Shift+Backspace"


STEP_FORWARD_KEYS = "Meta+Shift+Y" if sys.platform == "darwin" else "Ctrl+Shift+Y"
CHECKPOINTS_KEYS = "Ctrl+Shift+X"


def toggle_panel_key_text() -> str:





    from qgis.PyQt.QtGui import QKeySequence

    sequence = QKeySequence(TOGGLE_PANEL_KEYS)
    try:
        from qgis.PyQt.QtWidgets import QAction
        from qgis.utils import iface

        action = iface.mainWindow().findChild(QAction, TOGGLE_DOCK_OBJECT)
        if action is not None:
            sequence = action.shortcut()
    except (ImportError, AttributeError, RuntimeError, TypeError):
        pass
    return sequence.toString(QKeySequence.SequenceFormat.NativeText)

def tr(text: str, disambiguation: str | None = None, n: int = -1) -> str:

    return QCoreApplication.translate(TR_CONTEXT, text, disambiguation, n)


def exec_dialog(dialog):














    return QDialog.exec(dialog)


def popup_menu(menu, pos) -> None:








    round_popup_corners(menu)
    menu.aboutToHide.connect(menu.deleteLater)
    menu.popup(pos)


def round_popup_corners(popup) -> None:












    from qgis.PyQt.QtCore import QT_VERSION, Qt

    try:
        if popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
            return
        if QT_VERSION < 0x060000 or not isinstance(popup, QMenu):
            flags = popup.windowFlags()
            if not flags & Qt.WindowType.FramelessWindowHint:
                popup.setWindowFlags(flags | Qt.WindowType.FramelessWindowHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    except (AttributeError, RuntimeError, TypeError):
        pass


def paint_styled_ground(widget) -> None:





    from qgis.PyQt.QtGui import QPainter
    from qgis.PyQt.QtWidgets import QStyle, QStyleOption

    option = QStyleOption()
    option.initFrom(widget)
    painter = QPainter(widget)
    try:
        widget.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option, painter, widget)
    finally:
        painter.end()


UI_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(os.path.dirname(UI_DIR))
ICONS_DIR = os.path.join(PLUGIN_DIR, "icons")

PLUGIN_ICON_PATH = os.path.join(ICONS_DIR, "icon@4x.png")
TERRALAB_LOGO_PATH = os.path.join(ICONS_DIR, "terralab-logo.png")



_LEGACY_CACHE_DIR = os.path.normpath(os.path.expanduser("~/.qgis_ai_agent"))


def drop_legacy_cache_dir() -> bool:








    import shutil

    if _LEGACY_CACHE_DIR == PLUGIN_CACHE_DIR or not os.path.isdir(_LEGACY_CACHE_DIR):
        return True
    import stat

    failed = []

    def _retry(func, target, _exc):

        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            failed.append(target)

    try:
        shutil.rmtree(_LEGACY_CACHE_DIR, onexc=_retry)
    except TypeError:
        shutil.rmtree(_LEGACY_CACHE_DIR, onerror=_retry)
    except OSError:
        return False
    return not failed












LEARN_ITEMS = (
    {
        "kind": "video",
        "title": "Review of the AI Agent for QGIS plugin by TerraLab",
        "note": QT_TRANSLATE_NOOP("AIAgent", "Hans van der Kwast's review, recorded in QGIS."),
        "url": "https://youtu.be/QuVbzBfkacw",
        "thumbnail_url": f"{SITE_URL}/blog/ai-agent-complete-guide/video-tutorial-thumbnail.jpg",
        "translate": ("note",),
    },
    {
        "kind": "article",
        "title": QT_TRANSLATE_NOOP("AIAgent", "The complete AI Agent guide"),
        "note": QT_TRANSLATE_NOOP("AIAgent", "The panel from the first prompt to the finished map."),
        "url": TUTORIAL_URL,
        "thumbnail_url": f"{SITE_URL}/blog/ai-agent-complete-guide/og.jpg",
        "translate": ("title", "note"),
    },
)


def _built_in_learn_items() -> list:





    rows = []
    for item in LEARN_ITEMS:
        row = {k: v for k, v in item.items() if k != "translate"}
        for field in item.get("translate", ()):
            row[field] = tr(row[field])
        rows.append(row)
    return rows


def get_learn_items() -> list:





    return get_served_learn_items() or _built_in_learn_items()


def resolve_qt_enum(parent, scope: str | None, name: str):

    if scope:
        scoped = getattr(getattr(parent, scope, None), name, None)
        if scoped is not None:
            return scoped
    return getattr(parent, name)


def qt_enum_int(parent, scope: str | None, name: str, fallback: int) -> int:








    try:
        member = resolve_qt_enum(parent, scope, name)
    except AttributeError:
        return int(fallback)
    value = getattr(member, "value", member)
    return int(value) if isinstance(value, int) else int(fallback)


def _import_moved(name: str):

    for module in ("qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets"):
        found = getattr(importlib.import_module(module), name, None)
        if found is not None:
            return found
    raise ImportError(f"{name} not found in qgis.PyQt.QtGui or QtWidgets")


QAction = _import_moved("QAction")
QActionGroup = _import_moved("QActionGroup")
QShortcut = _import_moved("QShortcut")


def event_pos(event):

    getter = getattr(event, "position", None) or getattr(event, "localPos", None)
    getter = getter or getattr(event, "pos")  # noqa: B009
    point = getter()
    to_point = getattr(point, "toPoint", None)
    return to_point() if to_point is not None else point


def released_on(widget, event) -> bool:






    if event.button() != Qt.MouseButton.LeftButton or not widget.rect().contains(event_pos(event)):
        return False
    event.accept()
    return True


def format_reset_date(period_end_iso: str) -> str:









    from qgis.PyQt.QtCore import QDate, QDateTime, QLocale, Qt

    if not period_end_iso:
        return ""
    stamp = QDateTime.fromString(str(period_end_iso), Qt.DateFormat.ISODate)
    if not stamp.isValid():
        date = QDate.fromString(str(period_end_iso)[:10], "yyyy-MM-dd")
    else:
        date = stamp.toLocalTime().date()
    if not date.isValid():
        return ""

    locale = words_locale()
    pattern = day_pattern(year=date.year() != QDate.currentDate().year())

    if date.day() == 1 and locale.language() == QLocale.Language.French:
        pattern = pattern.replace("d ", "d'er' ", 1)
    return locale.toString(date, pattern)







_SCREEN_EDGE_MARGIN_PX = 48


def lightbox_window(anchor):


    if anchor is None:
        return None
    try:
        from qgis.utils import iface

        main = iface.mainWindow() if iface is not None else None
    except (ImportError, AttributeError, RuntimeError):
        main = None
    return main if main is not None else anchor.window()


def available_screen_rect(widget):

    try:
        screen = widget.screen()
    except (AttributeError, RuntimeError):
        screen = None
    if screen is None:
        from qgis.PyQt.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    available = screen.availableGeometry()
    return None if available.isEmpty() else available


def screen_area_at(point, widget=None):








    from qgis.PyQt.QtGui import QGuiApplication

    screen = None
    try:
        screen = QGuiApplication.screenAt(point)
    except (AttributeError, RuntimeError, TypeError):
        screen = None
    if screen is None and widget is not None:
        try:
            screen = widget.screen()
        except (AttributeError, RuntimeError):
            screen = None
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    area = screen.availableGeometry()
    return None if area.isEmpty() else area


def keep_on_screen(x: int, y: int, width: int, height: int, area) -> tuple:



    if area is None:
        return int(x), int(y)
    x = max(area.left(), min(int(x), area.left() + area.width() - int(width)))
    y = max(area.top(), min(int(y), area.top() + area.height() - int(height)))
    return int(x), int(y)



_POP_MS = 160


class _PopIn(QVariantAnimation):



    def __init__(self, popover):
        super().__init__(popover)
        self.setDuration(_POP_MS)
        self.setEasingCurve(QEasingCurve.Type.OutQuint)
        self.setStartValue(0.0)
        self.setEndValue(1.0)
        self._at = popover.pos()
        self._from = 0
        self.valueChanged.connect(self._step)

    def play(self, offset: int) -> None:
        self.stop()
        self._at, self._from = self.parent().pos(), offset
        self.start()

    def _step(self, value) -> None:
        popover, share = self.parent(), float(value)
        popover.setWindowOpacity(share)
        if self._from:
            popover.move(self._at.x(), round(self._at.y() + (1.0 - share) * self._from))


def settle_hover(widget) -> None:





    inside = widget.rect().contains(widget.mapFromGlobal(QCursor.pos()))
    if widget.underMouse() != inside:
        widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, inside)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def show_popped(popover, below: bool) -> None:








    at = popover.pos()
    owner = popover.parentWidget()
    by_hand = owner is None or owner.rect().contains(owner.mapFromGlobal(QCursor.pos()))
    entrance = popover.findChild(_PopIn) or _PopIn(popover)
    entrance.stop()
    popover.setWindowOpacity(0.0 if by_hand else 1.0)
    popover.show()
    if popover.pos() != at:
        popover.move(at)
    if by_hand:
        entrance.play(0 if prefers_reduced_motion() else (-6 if below else 6))


def place_by_box(popover, box, right: bool = False) -> bool:






    width, height = popover.width(), popover.height()
    host = box
    while host.parentWidget() is not None and not isinstance(host, QDockWidget):
        host = host.parentWidget()
    host_at = host.mapToGlobal(QPoint(0, 0))
    at = box.mapToGlobal(QPoint(0, 0))
    x = at.x() + box.width() - width if right else at.x()
    x = max(host_at.x() + 4, min(x, host_at.x() + host.width() - 4 - width))
    y = at.y() + box.height() + 6
    area = screen_area_at(at, box)
    floor = host_at.y() + host.height() - 4
    if area is not None:
        floor = min(floor, area.top() + area.height())
    below = y + height <= floor
    if not below:
        y = at.y() - height - 6
    popover.move(QPoint(*keep_on_screen(x, y, width, height, area)))
    return below


def size_within_screen(dialog, width: int, height: int,
                       min_width: int = 0, min_height: int = 0) -> None:

    available = available_screen_rect(dialog)
    if available is not None:
        cap_w = max(1, available.width() - _SCREEN_EDGE_MARGIN_PX)
        cap_h = max(1, available.height() - _SCREEN_EDGE_MARGIN_PX)
        width, height = min(width, cap_w), min(height, cap_h)
        min_width, min_height = min(min_width, cap_w), min(min_height, cap_h)
    if min_width or min_height:
        dialog.setMinimumSize(min_width, min_height)
    dialog.resize(width, height)


def watch_screen_changes(root) -> bool:





















    from qgis.PyQt.QtCore import QEvent
    from qgis.PyQt.QtWidgets import QApplication, QWidget

    try:
        window = root.window()
        handle = window.windowHandle() if window is not None else None
    except (AttributeError, RuntimeError):
        return False
    if handle is None:
        return False
    if getattr(root, "_screen_watch", None) is not None:
        return True

    def repaint(_screen=None):
        try:
            targets = [root] + list(root.findChildren(QWidget))
        except RuntimeError:
            return
        for widget in targets:
            try:
                QApplication.sendEvent(widget, QEvent(QEvent.Type.PaletteChange))
            except (RuntimeError, TypeError):
                continue

    try:
        handle.screenChanged.connect(repaint)
    except (AttributeError, TypeError):
        return False


    root._screen_watch = repaint
    return True
