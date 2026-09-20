# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




























































from __future__ import annotations

import contextlib
import hashlib
import math
import os
import shutil
import threading
import time
import uuid

from qgis.core import (
    Qgis,
    QgsLayoutItemMap,
    QgsLayoutItemMapItem,
    QgsLayoutObject,
    QgsMapClippingUtils,
    QgsMapLayerRenderer,
    QgsMapLayerStyle,
    QgsMapRendererCustomPainterJob,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsPluginLayer,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsRenderContext,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QPoint, QPointF, QRectF, QSizeF, Qt, QTimer
from qgis.PyQt.QtGui import QImage, QPainter
from qgis.PyQt.QtWidgets import QGraphicsItem

from ..core.background import run_on_main_thread
from ..core.logger import log_warning
from ..core.qt_compat import enum_member



_MAX_PIXELS = 48_000_000



_MAX_SPILL_PIXELS = 256_000_000

_AT_ONCE = 4
_PICTURE_TYPE = "ai_agent_layout_picture"



_JOBS: dict = {}


def _flag(name: str):
    return enum_member(Qgis, "MapSettingsFlag", name)


def _active(item, prop: str) -> bool:
    try:
        return bool(item.dataDefinedProperties().isActive(enum_member(QgsLayoutObject, "DataDefinedProperty", prop)))
    except (AttributeError, RuntimeError, TypeError):
        return True


def _style_hash(layer, override, cache: dict | None = None) -> str:




    key = (layer.id(), override)
    if cache is not None and key in cache:
        return cache[key]
    try:
        if override is None:
            style = QgsMapLayerStyle()
            style.readFromLayer(layer)
            override = style.xmlData()
        blend = layer.blendMode()
        text = f"{layer.source()}|{int(getattr(blend, 'value', blend))}|{override}"
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    except Exception:  # noqa: BLE001
        digest = ""
    if cache is not None:
        cache[key] = digest
    return digest


def _is_group(layer) -> bool:
    try:
        from qgis.core import QgsGroupLayer
    except ImportError:
        return False
    return isinstance(layer, QgsGroupLayer)


def _pictured(layer) -> bool:

    if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        return False
    renderer = layer.renderer()
    return renderer is not None and str(renderer.type() or "") != "contour"


def _raster_type():

    scoped = getattr(getattr(Qgis, "LayerType", None), "Raster", None)
    if scoped is not None:
        return scoped
    from qgis.core import QgsMapLayerType
    return QgsMapLayerType.RasterLayer


def _clip_path(settings, layer):




    context = QgsRenderContext.fromMapSettings(settings)
    regions = QgsMapClippingUtils.collectClippingRegionsForLayer(context, layer)
    if not regions:
        return None
    path, clips = QgsMapClippingUtils.calculatePainterClipRegion(regions, context, _raster_type())
    return path if clips else None


def page_dpi(width_px: int, height_px: int, page_width: float, page_height: float, inch: float) -> int:






    resolution = (width_px / page_width + height_px / page_height) / 2.0 * inch
    dots_per_metre = int(resolution / 25.4 * 1000 + 0.5)
    return int(dots_per_metre * 0.0254 + 0.5)


def paint_dpi(fmt: str, dpi: float) -> int:


    if fmt in ("pdf", "svg"):
        return int(math.floor(float(dpi) + 0.5))
    dots_per_metre = int(math.floor(float(dpi) / 25.4 * 1000 + 0.5))
    return int(math.floor(dots_per_metre * 0.0254 + 0.5))


@contextlib.contextmanager
def _export_flags(layout, flags=None):



    if flags is None:
        yield
        return
    context = layout.renderContext()
    old_flags = context.flags()
    blocked = context.blockSignals(True)
    try:
        context.setFlags(flags)
        yield
    finally:
        context.setFlags(old_flags)
        context.blockSignals(blocked)


def _map_settings(item, dpi: int, extent=None):




    extent = QgsRectangle(extent) if extent is not None else item.extent()
    width = extent.width()
    to_layout = item.rect().width() / width if width > 0 else 1.0
    size = QSizeF(extent.width() * to_layout, extent.height() * to_layout) * (dpi / 25.4)
    settings = QgsMapSettings(item.mapSettings(extent, size, dpi, True))



    settings.setRendererUsage(enum_member(Qgis, "RendererUsage", "Export"))
    settings.setDpiTarget(-1)
    settings.setDevicePixelRatio(1.0)
    return settings


def _view_key(uid: str, settings) -> str:

    size = settings.outputSize()
    crs = settings.destinationCrs()
    key = (f"{uid}|{settings.visibleExtent().toString(9)}|{size.width()}x{size.height()}|"
           f"{settings.rotation():.9g}|{settings.outputDpi():.6g}|{crs.authid() or crs.toWkt()}")
    try:
        if settings.isTemporal():
            span = settings.temporalRange()
            key += f"|{span.begin().toString()}|{span.end().toString()}"
    except (AttributeError, RuntimeError):
        pass
    return key


def _picture_settings(settings, layer):


    one = QgsMapSettings(settings)
    one.setLayers([layer])
    overrides = settings.layerStyleOverrides()
    one.setLayerStyleOverrides({layer.id(): overrides[layer.id()]} if layer.id() in overrides else {})
    one.setFlag(_flag("DrawLabeling"), False)
    with contextlib.suppress(AttributeError):
        one.setClippingRegions([])
    return one


def page_device(layout, page_index: int, device: dict):










    from qgis.core import QgsLayoutMeasurement

    try:
        page = layout.pageCollection().page(page_index)
        rect, origin = page.rect(), QPointF(page.pos())
        page_width, page_height = rect.width(), rect.height()
        inch = layout.convertToLayoutUnits(QgsLayoutMeasurement(25.4))
        if "size" in device:
            width, height = device["size"]
            resolution = (width / page_width + height / page_height) / 2.0 * inch
        else:
            resolution = float(device["dpi"])
            width, height = int(resolution * page_width / inch), int(resolution * page_height / inch)
        dots_per_metre = int(math.floor(resolution / 25.4 * 1000 + 0.5))
        return min(width / page_width, height / page_height), origin, dots_per_metre
    except (AttributeError, KeyError, RuntimeError, TypeError, ZeroDivisionError):
        return None


def _main_annotation_id() -> str:

    try:
        return QgsProject.instance().mainAnnotationLayer().id()
    except (AttributeError, RuntimeError):
        return ""


def _layer_stamp(layer, override, cache: dict) -> str:


    stamp = _style_hash(layer, override, cache)
    if isinstance(layer, QgsVectorLayer):
        try:
            stack = layer.undoStack()
            stamp += f"|{layer.isEditable()}|{stack.index() if stack is not None else -1}"
        except RuntimeError:
            stamp += "|?"
    return stamp


def _export_mode(settings, layout) -> None:



    context = layout.renderContext()
    method = context.simplifyMethod()
    hints = method.simplifyHints()
    settings.setFlag(_flag("UseRenderingOptimization"), bool(getattr(hints, "value", hints)))
    settings.setSimplifyMethod(method)
    with contextlib.suppress(AttributeError):
        settings.setMaskSettings(context.maskSettings())
    force = False
    with contextlib.suppress(AttributeError, RuntimeError, TypeError):
        force = bool(QgsLayoutItemMap.settingForceRasterMasks.value())
    settings.setFlag(_flag("ForceRasterMasks"), force)


def _overviews_on_top(item) -> bool:



    stack = item.overviews()
    top = enum_member(QgsLayoutItemMapItem, "StackingPosition", "StackAboveMapLabels")
    for index in range(stack.size()):
        overview = stack.overview(index)
        if overview.enabled() and overview.stackingPosition() != top:
            return False
    return True


def _blends_effects(layer) -> bool:



    normal = enum_member(QPainter, "CompositionMode", "CompositionMode_SourceOver")
    renderer = layer.renderer() if isinstance(layer, QgsVectorLayer) else None
    if renderer is None:
        return False
    for symbol in renderer.symbols(QgsRenderContext()):
        pending = [symbol]
        while pending:
            current = pending.pop()
            for symbol_layer in current.symbolLayers():
                effect = symbol_layer.paintEffect()
                effects = [effect] if effect is not None and effect.enabled() else []
                while effects:
                    one = effects.pop()
                    if hasattr(one, "effectList"):
                        effects.extend(e for e in one.effectList() if e.enabled())
                    elif hasattr(one, "blendMode") and one.blendMode() != normal:
                        return True
                sub = symbol_layer.subSymbol()
                if sub is not None:
                    pending.append(sub)
    return False


def _whole_view(plan: dict, layout, item, settings, overrides: dict):









    device = plan.get("device")
    if not device:
        return None
    try:
        if item.containsAdvancedEffects() or item.blendMode() != enum_member(
                QPainter, "CompositionMode", "CompositionMode_SourceOver"):
            return None
        moved = item.sceneTransform()
        if (moved.m11(), moved.m12(), moved.m21(), moved.m22()) != (1.0, 0.0, 0.0, 1.0):
            return None
        if not _overviews_on_top(item):
            return None
        annotation = _main_annotation_id()
        if any(layer.id() == annotation and not layer.isEmpty() for layer in settings.layers()):
            return None
        layers = [layer for layer in settings.layers() if layer.id() != annotation]
        if not layers or all(_pictured(layer) for layer in layers) or any(map(_blends_effects, layers)):
            return None
        placed = page_device(layout, item.page(), device)
        if placed is None:
            return None
        scale, origin, dots_per_metre = placed
        if int(dots_per_metre * 0.0254 + 0.5) != plan["dpi"]:
            return None
        corner = item.mapToScene(QPointF(0.0, 0.0))
        tx, ty = (corner.x() - origin.x()) * scale, (corner.y() - origin.y()) * scale
        left, top = math.floor(tx) - 1, math.floor(ty) - 1
        rect = item.rect()
        width = int(math.ceil(tx + rect.width() * scale)) + 1 - left
        height = int(math.ceil(ty + rect.height() * scale)) + 1 - top
        whole = QgsMapSettings(settings)
        whole.setLayers(layers)
        _export_mode(whole, layout)
        styles = {layer.id(): _layer_stamp(layer, overrides.get(layer.id()), plan["styles"]) for layer in layers}
        names = [layer.name() for layer in layers if not _pictured(layer)]
    except (AttributeError, RuntimeError, TypeError) as exc:
        log_warning(f"Layout map not drawn ahead whole: {exc}")
        return None
    box = settings.visibleExtent()
    picture = {"id": f"whole:{item.uuid()}", "name": ", ".join(names[:3]) + (" ..." if len(names) > 3 else ""),
               "whole": True, "ids": list(styles), "styles": styles, "settings": whole,
               "blend": enum_member(QPainter, "CompositionMode", "CompositionMode_SourceOver"),
               "device": {"tx": tx, "ty": ty, "left": left, "top": top, "width": width, "height": height,
                          "scale": scale * 25.4 / plan["dpi"], "dpm": dots_per_metre},
               "pixels": width * height, "done": threading.Event(), "image": None, "file": None,
               "ready": False, "errors": []}
    return {"uuid": item.uuid(), "crs": settings.destinationCrs(), "pictures": [picture], "whole": True,
            "box": (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())}


def new_plan(dpi: int, device: dict | None = None) -> dict:


    return {"dpi": int(dpi), "views": {}, "maps": set(), "pictures": [], "queue": [], "pixels": 0,
            "spill": None, "unfinished": [], "stopped": False, "closed": False, "styles": {},
            "device": device}


def _stacked_overview(item) -> bool:

    stack = item.overviews()
    above = enum_member(QgsLayoutItemMapItem, "StackingPosition", "StackAboveMapLayer")
    below = enum_member(QgsLayoutItemMapItem, "StackingPosition", "StackBelowMapLayer")
    for index in range(stack.size()):
        overview = stack.overview(index)
        if overview.enabled() and overview.stackingPosition() in (above, below):
            return True
    return False


def _eligible_map(item, pages) -> bool:
    try:
        if item.parentItem() is not None:
            return False
        if pages is not None and item.page() not in pages:
            return False
        if not item.isVisible() or item.excludeFromExports():
            return False

        if _active(item, "MapLayers"):
            return False


        if _stacked_overview(item):
            return False


        return not any(_is_group(layer) for layer in item.layersToRender())
    except (AttributeError, RuntimeError, TypeError):
        return False


def add_views(plan: dict, layout, pages=None, flags=None, extents=None) -> int:






    added = 0
    dpi = plan["dpi"]
    try:
        if QgsProject.instance().elevationShadingRenderer().isActive():
            return 0
    except (AttributeError, RuntimeError):
        pass
    with _export_flags(layout, flags):
        for item in layout.items():
            if not isinstance(item, QgsLayoutItemMap) or not _eligible_map(item, pages):
                continue
            try:
                uid = item.uuid()
                extent = (extents or {}).get(uid)
                settings = _map_settings(item, dpi, extent)
                key = _view_key(uid, settings)
                if key in plan["views"]:
                    continue
                size = settings.outputSize()
                if size.width() <= 0 or size.height() <= 0:
                    continue
                overrides = settings.layerStyleOverrides()
                rasters = [layer for layer in settings.layers() if _pictured(layer)]
            except (AttributeError, RuntimeError, TypeError) as exc:
                log_warning(f"Layout map not drawn ahead: {exc}")
                continue
            whole = _whole_view(plan, layout, item, settings, overrides)
            if whole is not None:
                plan["views"][key] = whole
                plan["pictures"].extend(whole["pictures"])
                plan["maps"].add(uid)
                added += whole["pictures"][0]["pixels"]
                continue
            if not rasters:
                continue
            box = settings.visibleExtent()
            view = {"uuid": uid, "crs": settings.destinationCrs(), "pictures": [],
                    "box": (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())}
            pixels = size.width() * size.height()
            for layer in rasters:
                picture = {"id": layer.id(), "name": layer.name(), "blend": layer.blendMode(),
                           "style": _style_hash(layer, overrides.get(layer.id()), plan["styles"]),
                           "settings": _picture_settings(settings, layer), "pixels": pixels,
                           "done": threading.Event(), "image": None, "file": None, "ready": False,
                           "errors": []}
                view["pictures"].append(picture)
                plan["pictures"].append(picture)
                added += pixels
            plan["views"][key] = view
            plan["maps"].add(uid)
    return added


def _allow(plan: dict) -> None:


    total = sum(picture["pixels"] for picture in plan["pictures"])
    allowed = total
    if total > _MAX_PIXELS:
        allowed = _MAX_PIXELS
        try:
            from ..core.policy import AGENT_TMP_DIR, create_managed_temp_dir

            free = shutil.disk_usage(AGENT_TMP_DIR if os.path.isdir(AGENT_TMP_DIR) else os.path.expanduser("~")).free
            on_disk = min(_MAX_SPILL_PIXELS, free // 16)
            if on_disk > _MAX_PIXELS:
                plan["spill"] = create_managed_temp_dir("layout")
                allowed = on_disk
        except (OSError, ImportError) as exc:
            log_warning(f"Layout pictures kept in memory only: {exc}")
    if total > allowed:
        kept, used = {}, 0
        for key, view in plan["views"].items():
            cost = sum(picture["pixels"] for picture in view["pictures"])
            if used + cost > allowed:
                continue
            kept[key] = view
            used += cost
        plan["views"] = kept
        plan["pictures"] = [picture for view in kept.values() for picture in view["pictures"]]
        plan["maps"] = {view["uuid"] for view in kept.values()}
    plan["pixels"] = sum(picture["pixels"] for picture in plan["pictures"])


def launch(plan: dict) -> dict:

    _allow(plan)
    plan["queue"] = list(plan["pictures"])
    for _ in range(_AT_ONCE):
        _next(plan)
    return plan


def start(layout, pages, dpi: int, flags=None, extents=None, device=None) -> dict:





    plan = new_plan(dpi, device)
    add_views(plan, layout, pages, flags, extents)
    return launch(plan)


def _next(plan: dict) -> None:

    queue = plan.get("queue")
    if plan.get("closed") or not queue:
        return
    _start_job(plan, queue.pop(0))


def _start_whole(plan: dict, picture: dict) -> None:


    device = picture["device"]
    image = QImage(device["width"], device["height"], enum_member(QImage, "Format", "Format_ARGB32_Premultiplied"))

    image.setDotsPerMeterX(device["dpm"])
    image.setDotsPerMeterY(device["dpm"])
    image.fill(0)
    painter = QPainter(image)
    painter.translate(device["tx"] - device["left"], device["ty"] - device["top"])
    painter.scale(device["scale"], device["scale"])
    job = QgsMapRendererCustomPainterJob(picture.pop("settings"), painter)
    key = id(job)
    _JOBS[key] = job
    picture["key"] = key

    def finished():
        try:
            painter.end()
            ended = _JOBS.get(key)
            if ended is not None:
                picture["image"] = image
                picture["errors"] = [str(error.message) for error in ended.errors()]
        finally:
            picture["done"].set()
            QTimer.singleShot(0, lambda: _JOBS.pop(key, None))
            _next(plan)

    job.finished.connect(finished)
    job.start()


def _start_job(plan: dict, picture: dict) -> None:

    if picture.get("whole"):
        _start_whole(plan, picture)
        return
    job = QgsMapRendererParallelJob(picture.pop("settings"))
    key = id(job)
    _JOBS[key] = job
    picture["key"] = key

    def finished():
        try:
            ended = _JOBS.get(key)
            if ended is not None:
                picture["image"] = ended.renderedImage()
                picture["errors"] = [str(error.message) for error in ended.errors()]
        finally:
            picture["done"].set()
            QTimer.singleShot(0, lambda: _JOBS.pop(key, None))
            _next(plan)

    job.finished.connect(finished)
    job.start()


def _cancel(plan: dict) -> None:

    plan["closed"] = True
    plan["queue"] = []
    for picture in plan.get("pictures") or ():
        job = _JOBS.get(picture.get("key"))
        if job is not None and not picture["done"].is_set():
            try:
                job.cancelWithoutBlocking()
            except RuntimeError:  # nosec B110
                pass


def _finish_inline(plan: dict) -> None:

    for picture in plan["pictures"]:
        if picture["done"].is_set():
            continue
        if "key" not in picture:
            with contextlib.suppress(ValueError):
                plan["queue"].remove(picture)
            _start_job(plan, picture)
        job = _JOBS.get(picture.get("key"))
        if job is not None:
            job.waitForFinished()


def wait(plan: dict, budget_s: float) -> dict:





    from ..core.background import on_main_thread
    from .advanced_render import _wait_or_stop

    pictures = (plan or {}).get("pictures") or []
    if not pictures:
        return plan
    if on_main_thread():
        _finish_inline(plan)
    deadline = time.monotonic() + max(1.0, float(budget_s))
    for picture in pictures:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        if _wait_or_stop(picture["done"], left):
            plan["stopped"] = True
            break
        _keep(picture, plan.get("spill"))
    late = [picture["name"] for picture in pictures if not picture["done"].is_set()]
    if late or plan["stopped"]:
        plan["unfinished"] = sorted(set(late))
        run_on_main_thread(_cancel, plan, timeout=10)
    return plan


def _keep(picture: dict, spill) -> None:




    image, picture["image"] = picture.get("image"), None
    if image is None or image.isNull():
        return
    try:




        alpha = not picture.get("whole") and image.format() in (
            enum_member(QImage, "Format", "Format_ARGB32_Premultiplied"),
            enum_member(QImage, "Format", "Format_ARGB32"))
        if alpha and image.constBits().asstring(image.sizeInBytes())[3::4].count(255) == \
                image.width() * image.height():
            image.reinterpretAsFormat(enum_member(QImage, "Format", "Format_RGB32"))
        if spill:
            path = os.path.join(spill, f"{uuid.uuid4().hex}.raw")
            with open(path, "wb") as handle:
                handle.write(image.constBits().asstring(image.sizeInBytes()))
            picture["file"] = path
            picture["shape"] = (image.width(), image.height(), image.bytesPerLine(), image.format())
        else:
            picture["image"] = image
        picture["ready"] = True
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layout picture not kept: {exc}")


def _image_of(picture: dict):

    image = picture.get("image")
    if image is not None or not picture.get("file"):
        return image
    try:
        with open(picture["file"], "rb") as handle:
            raw = handle.read()
        width, height, line, fmt = picture["shape"]
        return QImage(raw, width, height, line, fmt).copy()
    except (OSError, ValueError, TypeError) as exc:
        log_warning(f"Layout picture not read back: {exc}")
        return None


class _PictureRenderer(QgsMapLayerRenderer):




    def __init__(self, layer_id, context, image, clip=None, place=None):
        super().__init__(layer_id, context)
        self._image = image
        self._clip = clip
        self._place = place

    def render(self):
        image = self._image
        if image is None or image.isNull():
            return True
        painter = self.renderContext().painter()
        painter.save()
        try:
            if self._place is not None:



                painter.resetTransform()
                painter.drawImage(QPoint(*self._place), image)
                return True
            if self._clip is not None:
                painter.setClipPath(self._clip, enum_member(Qt, "ClipOperation", "IntersectClip"))
            painter.drawImage(QPointF(0.0, 0.0), image)
        finally:
            painter.restore()
        return True


class _PictureLayer(QgsPluginLayer):



    def __init__(self, picture: dict, view: dict, clip=None):
        super().__init__(_PICTURE_TYPE, picture["name"])
        self._picture = picture
        self._view = view
        self._clip = clip
        self.setCrs(view["crs"])
        self.setBlendMode(picture["blend"])
        self.setValid(True)

    def createMapRenderer(self, context):  # noqa: N802
        device = self._picture.get("device") if self._picture.get("whole") else None
        place = (device["left"], device["top"]) if device else None
        return _PictureRenderer(self.id(), context, _image_of(self._picture), self._clip, place)

    def extent(self):
        return QgsRectangle(*self._view["box"])

    def setTransformContext(self, context):  # noqa: N802
        pass

    def clone(self):
        return _PictureLayer(self._picture, self._view, self._clip)


def applies(layout, plan, dpi) -> bool:


    if not plan or int(dpi) != plan["dpi"]:
        return False
    for item in layout.items():
        if isinstance(item, QgsLayoutItemMap) and item.uuid() in plan["maps"]:
            try:
                if _view_key(item.uuid(), _map_settings(item, plan["dpi"])) in plan["views"]:
                    return True
            except (AttributeError, RuntimeError, TypeError):
                continue
    return False


class _Switch(QGraphicsItem):


    def __init__(self, rect, flip):
        super().__init__()
        self._rect = QRectF(rect)
        self._flip = flip

    def boundingRect(self):  # noqa: N802
        return self._rect

    def paint(self, painter, option, widget=None):  # noqa: ARG002
        try:
            self._flip(painter)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Layout map not switched: {exc}")


def drawn(layouts, plan, draw, dpi=None):






    if not plan or not plan.get("views") or (dpi is not None and int(dpi) != plan["dpi"]):
        return draw()
    if not isinstance(layouts, (list, tuple)):
        layouts = [layouts]
    state = {"swapped": {}, "keep": [], "styles": {}}
    switches = []
    try:
        for layout in layouts:
            for item in layout.items():
                if (isinstance(item, QgsLayoutItemMap) and item.uuid() in plan["maps"]
                        and item.parentItem() is None):
                    _switches(item, plan, state, switches)
        return draw()
    finally:
        for switch in switches:
            switch.setParentItem(None)
            scene = switch.scene()
            if scene is not None:
                scene.removeItem(switch)
        for item, own in list(state["swapped"].values()):
            _restore(item, own)

        state["swapped"].clear()
        state["keep"].clear()
        switches.clear()


def _swap(item, layers: list, overrides: dict) -> tuple:






    own = (list(item.layers()), item.followVisibilityPreset(), item.keepLayerStyles(),
           dict(item.layerStyleOverrides()))
    blocked = item.blockSignals(True)
    try:
        if own[1]:
            item.setFollowVisibilityPreset(False)
            item.setKeepLayerStyles(True)
            item.setLayerStyleOverrides(overrides)
        item.setLayers(layers)
    finally:
        item.blockSignals(blocked)
    return own


def _restore(item, own: tuple) -> None:
    layers, follows, keeps, overrides = own
    blocked = item.blockSignals(True)
    try:
        item.setLayers(layers)
        if follows:
            item.setLayerStyleOverrides(overrides)
            item.setKeepLayerStyles(keeps)
            item.setFollowVisibilityPreset(True)
    finally:
        item.blockSignals(blocked)


def _whole_stand_in(item, view: dict, painter, overrides: dict, state: dict):



    picture = view["pictures"][0]
    if not picture.get("ready") or painter is None:
        return None
    device = picture["device"]
    try:
        placed = painter.combinedTransform()
        dots_per_mm = painter.device().logicalDpiX() / 25.4
        scale = placed.m11() / dots_per_mm
        corner = placed.map(QPointF(0.0, 0.0))
        if (placed.m12() or placed.m21() or abs(placed.m22() - placed.m11()) > 1e-9
                or abs(scale - device["scale"]) > 1e-9
                or abs(corner.x() - device["tx"]) > 1e-6 or abs(corner.y() - device["ty"]) > 1e-6):
            return None
        annotation = _main_annotation_id()
        current = [layer for layer in item.layersToRender() if layer.id() != annotation]
        if [layer.id() for layer in current] != picture["ids"]:
            return None
        for layer in current:
            if _layer_stamp(layer, overrides.get(layer.id()), state["styles"]) != picture["styles"][layer.id()]:
                return None
    except (AttributeError, RuntimeError, TypeError, ZeroDivisionError) as exc:
        log_warning(f"Layout map drawn as before, its picture not checked: {exc}")
        return None
    return _PictureLayer(picture, view)


def _switches(item, plan: dict, state: dict, into: list) -> None:













    slot = id(item)

    def on(painter):
        if slot in state["swapped"]:
            return

        device = painter.device() if painter is not None else None
        dpi = int(device.logicalDpiX()) if device is not None else plan["dpi"]
        if dpi != plan["dpi"]:
            return
        settings = _map_settings(item, dpi)
        view = plan["views"].get(_view_key(item.uuid(), settings))
        if view is None:
            return
        overrides = settings.layerStyleOverrides()
        if view.get("whole"):
            stand_in = _whole_stand_in(item, view, painter, overrides, state)
            if stand_in is not None:
                state["keep"].append(stand_in)
                state["swapped"][slot] = (item, _swap(item, [stand_in], overrides))
            return
        current = item.layersToRender()
        by_id = {layer.id(): layer for layer in current}
        stand_in = {}
        for picture in view["pictures"]:
            layer = by_id.get(picture["id"])
            if (layer is None or not picture.get("ready")
                    or _style_hash(layer, overrides.get(layer.id()), state["styles"]) != picture["style"]):
                continue
            try:
                clip = _clip_path(settings, layer)
            except (AttributeError, RuntimeError, TypeError) as exc:
                log_warning(f"Layout raster drawn as before, its clip not read: {exc}")
                continue
            stand_in[picture["id"]] = _PictureLayer(picture, view, clip)
        if not stand_in:
            return
        state["keep"].extend(stand_in.values())
        state["swapped"][slot] = (item, _swap(item, [stand_in.get(layer.id(), layer) for layer in current],
                                              overrides))

    def off(_painter):
        swapped = state["swapped"].pop(slot, None)
        if swapped is not None:
            _restore(item, swapped[1])

    behind_flag = enum_member(QGraphicsItem, "GraphicsItemFlag", "ItemStacksBehindParent")
    children = item.childItems()
    behind = [child for child in children if child.flags() & behind_flag]
    front = [child for child in children if not child.flags() & behind_flag]
    rect = item.boundingRect()
    first, last = _Switch(rect, on), _Switch(rect, off)
    first.setFlag(behind_flag, True)
    if behind:
        first.setZValue(behind[-1].zValue())
    first.setParentItem(item)
    into.append(first)
    last.setParentItem(item)
    into.append(last)
    if front:
        last.setZValue(front[0].zValue())
        last.stackBefore(front[0])


def discard(plan) -> None:


    if not plan:
        return
    plan["closed"] = True
    for picture in plan.get("pictures") or ():
        picture["image"] = None
        picture["ready"] = False
    spill, plan["spill"] = plan.get("spill"), None
    if spill:
        from ..core.host_platform import remove_tree

        left = remove_tree(spill)
        if left:
            log_warning(f"Layout pictures not removed: {len(left)} in {spill}")






_HELD: dict = {}


_WAIT_SHARE = 0.6


def take(name: str):

    return _HELD.pop(name, None)


def _start_named(name: str, view: tuple):

    dpi, flags, extents = view[:3]
    image_dpi = view[4] if len(view) > 4 else None
    layout = QgsProject.instance().layoutManager().layoutByName(name)
    if layout is None or getattr(layout, "pageCollection", None) is None:
        return None
    return start(layout, set(range(layout.pageCollection().pageCount())), dpi, flags, extents,
                 {"dpi": image_dpi} if image_dpi else None)


def _held_for(name: str, plan, note):


    def held():
        discard(_HELD.pop(name, None))
        if plan:
            _HELD[name] = plan
        return note

    def release():
        if _HELD.get(name) is plan:
            del _HELD[name]
        discard(plan)

    held.release = release
    return held


def _waited(plan) -> None:
    from ..core import limits

    try:
        wait(plan, limits.current("CALL_MAX_SECONDS_BACKGROUND") * _WAIT_SHARE)
    except BaseException:
        discard(plan)
        raise


def prepare_export(args: dict):









    from . import raster_overviews
    from .advanced_layouts import export_view

    overviews = raster_overviews.prepare_layout(args)
    name = str(args.get("layout_name") or "")
    view = export_view(args)
    if view is None:
        return overviews
    if view[3] == "report":
        return _walked_for(name, _ReportWalk(name), view, overviews)

    def work():
        note = None
        finish = overviews() if overviews is not None else None
        if callable(finish):
            note = run_on_main_thread(finish, timeout=60)
        plan = run_on_main_thread(_start_named, name, view, timeout=30)
        _waited(plan)
        return _held_for(name, plan, note)

    return work





_WALK_SLICE_S = 0.05


def plan_iterator(walker, dpi: int, flags=None) -> dict:








    from ..core import net

    plan = new_plan(dpi)
    state = {"started": False, "done": False, "pixels": 0}

    def slice_():
        started = time.perf_counter()
        if not state["started"]:
            state["started"] = True
            if not walker.begin():
                state["done"] = True
                return
        while time.perf_counter() - started < _WALK_SLICE_S:
            if not walker.step():
                state["done"] = True
                return
            for layout in walker.layouts():
                state["pixels"] += add_views(plan, layout, None, flags)


            if state["pixels"] >= _MAX_SPILL_PIXELS:
                state["done"] = True
                return

    cancel = net.current_cancel_check()
    try:
        while not state["done"]:
            if cancel is not None and cancel():
                plan["stopped"] = True
                return plan
            run_on_main_thread(slice_, timeout=30)
    finally:
        if state["started"]:
            run_on_main_thread(walker.end, timeout=30)
    return run_on_main_thread(launch, plan, timeout=30)


class _AtlasWalk:



    def __init__(self, name: str, scale=None):
        self._name, self._scale = name, scale
        self._layout = self._atlas = None
        self._driven = []
        self._rendering = False
        self._next = 0

    def begin(self) -> bool:
        layout = QgsProject.instance().layoutManager().layoutByName(self._name)
        atlas = layout.atlas() if layout is not None and hasattr(layout, "atlas") else None
        if atlas is None:
            return False
        self._layout, self._atlas = layout, atlas
        self._driven = [(item, item.atlasScalingMode(), QgsRectangle(item.extent())) for item in layout.items()
                        if isinstance(item, QgsLayoutItemMap) and item.atlasDriven()]
        if self._scale is not None:
            self._scale(layout)
        self._rendering = bool(atlas.beginRender())
        return self._rendering

    def step(self) -> bool:
        if self._next >= self._atlas.count() or not self._atlas.seekTo(self._next):
            return False
        self._next += 1
        return True

    def layouts(self) -> list:
        return [self._layout]

    def end(self) -> None:
        if self._rendering:
            self._atlas.endRender()
        for item, mode, extent in self._driven:
            item.setAtlasScalingMode(mode)
            item.setExtent(extent)
        self._layout = self._atlas = None
        self._driven = []


class _ReportWalk:


    def __init__(self, name: str):
        self._name = name
        self._report = None
        self._rendering = False

    def begin(self) -> bool:
        report = QgsProject.instance().layoutManager().layoutByName(self._name)
        if report is None or getattr(report, "pageCollection", None) is not None:
            return False
        self._report = report
        self._rendering = bool(report.beginRender())
        return self._rendering

    def step(self) -> bool:
        return bool(self._report.next())

    def layouts(self) -> list:
        layout = self._report.layout()
        return [layout] if layout is not None else []

    def end(self) -> None:
        if self._rendering:
            self._report.endRender()
        self._report = None


def _walked_for(key: str, walker, view: tuple, overviews):


    dpi, flags = view[0], view[1]

    def work():
        note = None
        finish = overviews() if overviews is not None else None
        if callable(finish):
            note = run_on_main_thread(finish, timeout=60)
        plan = plan_iterator(walker, dpi, flags)
        if not plan.get("stopped"):
            _waited(plan)
        return _held_for(key, plan, note)

    return work


def prepare_atlas(args: dict):





    from . import raster_overviews
    from .harvest_layout import atlas_view

    overviews = raster_overviews.prepare_layout(args)
    name = str(args.get("layout_name") or "")
    view = atlas_view(args)
    if view is None:
        return overviews
    return _walked_for("atlas:" + name, _AtlasWalk(name, view[2]), view, overviews)
