# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

import os
import re
import time
import uuid

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsContrastEnhancement,
    QgsDataSourceUri,
    QgsFeatureRequest,
    QgsProject,
    QgsRasterLayer,
    QgsSingleBandGrayRenderer,
    QgsTask,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP
from qgis.utils import iface

from ..core import background, layer_order, limits
from ..core.follow import hold_view
from ..core.logger import log, log_warning
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._layers import layer_not_found, resolve_layer, wfs_feature_cap
from .data_tools import _avoid_reserved_name
from .processing_run import _PROCESSING_TASKS, _readers_of, _sweep_consumed_tasks, remove_layers

_REMOTE_PREFIXES = ("/vsicurl/", "http://", "https://", "/vsis3/", "/vsiaz/", "/vsigs/")


def register_harvest_view_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="set_layer_filter",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Filter {layer_name}[: {filter}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "filter": {
                    "type": "string",
                },
            },
            "required": ["layer_name", "filter"],
        },
        handler=_set_layer_filter,
    ))

    registry.register(Tool(
        name="zoom_to_selected",
        sets_view=True,
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Zoom to the selection[ of {layer_name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                },
            },
        },
        handler=_zoom_to_selected,
    ))

    registry.register(Tool(
        name="open_attribute_table",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Open the attribute table[ of {layer_name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "filter_expression": {
                    "type": "string",
                },
            },
            "required": ["layer_name"],
        },
        handler=_open_attribute_table,
    ))

    registry.register(Tool(
        name="create_hillshade",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Create a hillshade[ from {layer_name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                },
                "name": {
                    "type": "string",
                },
                "output_path": {
                    "type": "string",
                },
                "azimuth": {"type": "number", "minimum": 0, "maximum": 360},
                "altitude": {"type": "number", "minimum": 0, "maximum": 90},
                "z_factor": {
                    "type": "number",
                    "minimum": 1e-9,
                    "maximum": 1e9,
                },
                "band": {"type": "integer", "minimum": 1},
                "multidirectional": {
                    "type": "boolean",
                },
                "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                "overwrite": {"type": "boolean"},
            },
        },
        handler=_create_hillshade,
    ))





def _vector_layer_or_error(name_or_id: str):

    layer = resolve_layer(name_or_id)
    if layer is None:
        return None, layer_not_found(name_or_id)
    if not isinstance(layer, QgsVectorLayer):
        return None, tool_error(
            f"Layer {layer.name()!r} is not a vector layer.",
            "INVALID_ARGS",
            "list_layers gives the vector layers.",
        )
    return layer, None


def _feature_count(layer) -> int:

    count = layer.featureCount()
    if count is not None and count >= 0:
        return int(count)



    request = QgsFeatureRequest()
    request.setNoAttributes()
    request.setLimit(_COUNT_CAP)
    try:
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    except Exception:  # nosec B110
        pass
    return sum(1 for _ in layer.getFeatures(request))


_COUNT_CAP = 10_000


def _extent_dict(extent) -> dict:
    return {
        "xmin": extent.xMinimum(),
        "ymin": extent.yMinimum(),
        "xmax": extent.xMaximum(),
        "ymax": extent.yMaximum(),
    }





def _set_layer_filter(args: dict) -> dict:
    layer, error = _vector_layer_or_error(args["layer_name"])
    if error:
        return error
    expression = str(args.get("filter") or "").strip()
    if layer.isEditable():
        return tool_error(
            f"Layer {layer.name()!r} has an open edit session; QGIS refuses a filter change while editing.",
            "INVALID_ARGS",
            "qgis_edit_commit or qgis_edit_rollback ends the edit session; set_layer_filter "
            "runs after that.",
        )
    previous = layer.subsetString() or ""
    lifted = None
    if expression:
        layer, lifted = _lift_wfs_cap(layer)
        if layer is None:
            return layer_not_found(args["layer_name"])
    if not layer.setSubsetString(expression):
        if lifted is not None:
            layer.setSubsetString(previous)
        return tool_error(
            f"The provider rejected the filter {expression!r} on {layer.name()!r}.",
            "INVALID_ARGS",
            "get_layer_info gives the field names. OGR, GeoPackage and PostgreSQL layers take SQL "
            "WHERE syntax (double quotes around field names, single quotes around text); memory layers "
            "take a QGIS expression. validate_expression checks the expression.",
        )
    layer.triggerRepaint()
    count = _feature_count(layer)
    cap = wfs_feature_cap(layer)
    out = {
        "layer_id": layer.id(),
        "layer_name": layer.name(),
        "filter": layer.subsetString() or "",
        "previous_filter": previous,
        "cleared": expression == "",
        "feature_count": count if cap is None else min(count, cap),
    }
    if cap is not None:


        out["features_available"] = count


        layer_order.mark_truncated_count(layer, min(count, cap))
        if lifted is not None:
            out["max_features"] = lifted
        if count > cap:
            out["truncated"] = True
            out["warning"] = (f"The service holds {count:,} features under this filter and the layer loads "
                              f"{cap:,} of them, the first in the service's own order.")
            out["suggestion"] = "a narrower filter, or one part at a time, loads the rest."
    if expression and count == 0:
        out["_note"] = "The filter matches no feature: the layer shows nothing until the filter changes."
    return out


def _lift_wfs_cap(layer):

















    cap = wfs_feature_cap(layer)
    ceiling = int(limits.current("MAX_FEATURES_PER_CALL"))
    if cap is None or cap >= ceiling:
        return layer, None
    uri = QgsDataSourceUri(layer.source())
    uri.removeParam("maxNumFeatures")
    uri.setParam("maxNumFeatures", str(ceiling))
    layer_id = layer.id()
    copy = _read_again(layer, uri.uri(False))

    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is None or copy is None or not _put_in_place(layer, copy):
        return layer, None
    return copy, ceiling


def _read_again(layer, source: str):







    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    project = QgsProject.instance()
    context = QgsReadWriteContext()
    context.setPathResolver(project.pathResolver())
    context.setTransformContext(project.transformContext())
    document = QDomDocument("qgis")
    element = document.createElement("maplayer")
    document.appendChild(element)
    layer_id = layer.id()
    if not layer.writeLayerXml(element, document, context):
        return None
    written = element.firstChildElement("datasource")
    if written.isNull():
        return None
    address = document.createElement("datasource")
    address.appendChild(document.createTextNode(layer.encodedSource(source, context)))
    element.replaceChild(address, written)
    copy = QgsVectorLayer()
    try:
        read = copy.readLayerXml(element, context)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"set_layer_filter: {layer_id} not read again with its new cap: {exc}")
        return None
    if not read or not copy.isValid() or copy.id() != layer_id:
        return None
    return copy


def _put_in_place(old, new) -> bool:

















    from qgis.PyQt.QtXml import QDomDocument

    from ..core import layer_egress

    if _readers_of(old):
        return False
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    layer_id = old.id()
    node = root.findLayer(layer_id)
    parent = node.parent() if node is not None else None
    index = parent.children().index(node) if parent is not None else -1
    node = node.clone() if parent is not None else None
    order = [layer.id() for layer in root.customLayerOrder()]
    themes = None
    if project.mapThemeCollection().mapThemes():
        themes = QDomDocument("qgis")
        themes.appendChild(themes.createElement("qgis"))
        project.mapThemeCollection().writeXml(themes)
    ties = _ties_of(project, old)
    try:
        active = iface.activeLayer() is old
    except Exception:  # noqa: BLE001
        active = False
    checked = layer_egress.checked(layer_id)
    with layer_order.read_back():
        if not remove_layers([old]):
            return False
        project.addMapLayer(new, False)
        if node is not None:
            node.resolveReferences(project)
            parent.insertChildNode(index, node)
    new.resolveReferences(project)
    root.setCustomLayerOrder([project.mapLayer(i) for i in order if project.mapLayer(i) is not None])
    if themes is not None:
        project.mapThemeCollection().readXml(themes)
    _tie_again(project, new, ties)
    if active:
        try:
            iface.setActiveLayer(new)
        except Exception:  # nosec B110
            pass
    if checked:
        layer_egress.note_run_layers([new])
    return True


def _ties_of(project, layer) -> dict:

    from qgis.core import QgsLayoutItemLegend, QgsLayoutItemMap

    layer_id = layer.id()
    ties = {"maps": [], "atlases": [], "legends": []}
    for layout in project.layoutManager().printLayouts():
        atlas = layout.atlas()
        if atlas.coverageLayer() is layer:
            ties["atlases"].append((atlas, atlas.enabled()))
        for item in layout.items():
            if isinstance(item, QgsLayoutItemMap):
                locked = [kept.id() for kept in item.layers()]
                overrides = item.layerStyleOverrides()
                if layer_id in locked or layer_id in overrides:
                    ties["maps"].append((item, locked, overrides))
            elif isinstance(item, QgsLayoutItemLegend):

                ties["legends"].append(item)
    ties["relations"] = [relation for relation in project.relationManager().relations().values()
                         if layer_id in (relation.referencingLayerId(), relation.referencedLayerId())]
    ties["joins"] = [(other, info) for other in project.mapLayers().values()
                     if isinstance(other, QgsVectorLayer) and other.id() != layer_id
                     for info in other.vectorJoins() if info.joinLayerId() == layer_id]
    snapping = project.snappingConfig().individualLayerSettings(layer)
    ties["snapping"] = [snapping] if snapping.valid() else []
    return ties


def _tie_again(project, layer, ties: dict) -> None:


    def map_item(entry):
        item, locked, overrides = entry
        item.setLayers([project.mapLayer(i) for i in locked if project.mapLayer(i) is not None])
        item.setLayerStyleOverrides(overrides)

    def atlas(entry):
        coverage, enabled = entry
        coverage.setCoverageLayer(layer)
        coverage.setEnabled(enabled)

    def legend(item):
        item.model().rootGroup().resolveReferences(project)

    def relation(entry):
        entry.setReferencingLayer(entry.referencingLayerId())
        entry.setReferencedLayer(entry.referencedLayerId())
        project.relationManager().addRelation(entry)

    def join(entry):
        other, info = entry
        if not any(kept.joinLayerId() == layer.id() for kept in other.vectorJoins()):
            info.setJoinLayer(layer)
            other.addJoin(info)

    def snapping(settings):
        config = project.snappingConfig()
        config.setIndividualLayerSettings(layer, settings)
        project.setSnappingConfig(config)

    for kind, put_back in (("maps", map_item), ("atlases", atlas), ("legends", legend),
                           ("relations", relation), ("joins", join), ("snapping", snapping)):
        for entry in ties[kind]:
            try:
                put_back(entry)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"set_layer_filter: {kind} of {layer.name()!r} not put back: {exc}")





def _zoom_to_selected(args: dict) -> dict:
    name = args.get("layer_name")
    if name:
        layer, error = _vector_layer_or_error(name)
        if error:
            return error
    else:
        layer = iface.activeLayer()
        if layer is None:
            return tool_error("No active layer.", "INVALID_ARGS", "layer_name or set_active_layer names it.")
        if not isinstance(layer, QgsVectorLayer):
            return tool_error(
                f"The active layer {layer.name()!r} is not a vector layer.",
                "INVALID_ARGS",
                "layer_name must be a vector layer with a selection.",
            )
    selected = layer.selectedFeatureCount()
    if selected == 0:
        return tool_error(
            f"Layer {layer.name()!r} has no selected features.",
            "INVALID_ARGS",
            "select_by_attribute, select_by_geometry or select_features selects some.",
        )
    canvas = iface.mapCanvas()
    canvas.zoomToSelected(layer)
    canvas.refresh()
    reprojected = hold_view(canvas, prefer=layer.crs(), target=(layer.boundingBoxOfSelected(), layer.crs()))
    return {
        **({"project_crs_changed": reprojected} if reprojected else {}),
        "layer_id": layer.id(),
        "layer_name": layer.name(),
        "selected_count": selected,
        "extent": _extent_dict(canvas.extent()),
        "crs": canvas.mapSettings().destinationCrs().authid(),
        "scale": round(canvas.scale()),
    }





def _open_attribute_table(args: dict) -> dict:
    layer, error = _vector_layer_or_error(args["layer_name"])
    if error:
        return error
    expression = str(args.get("filter_expression") or "").strip()
    dialog = iface.showAttributeTable(layer, expression) if expression else iface.showAttributeTable(layer)
    if dialog is None:
        return tool_error(
            f"QGIS did not open the attribute table of {layer.name()!r}.",
            "ATTRIBUTE_TABLE_FAILED",
            "get_layer_info may show the layer invalid; accessibility_snapshot shows what is on screen.",
        )
    out = {
        "layer_id": layer.id(),
        "layer_name": layer.name(),
        "opened": True,
        "feature_count": _feature_count(layer),
        "selected_count": layer.selectedFeatureCount(),
        "window_title": dialog.windowTitle(),
    }
    if expression:
        out["filter_expression"] = expression
    subset = layer.subsetString()
    if subset:
        out["layer_filter"] = subset
    return out





def _stretch_algorithm():
    for owner in (
        QgsContrastEnhancement,
        getattr(QgsContrastEnhancement, "ContrastEnhancementAlgorithm", None),
        getattr(Qgis, "ContrastEnhancementAlgorithm", None),
    ):
        value = getattr(owner, "StretchToMinimumMaximum", None) if owner is not None else None
        if value is not None:
            return value
    return None


def _style_hillshade(layer, opacity: float):

    provider = layer.dataProvider()
    renderer = QgsSingleBandGrayRenderer(provider, 1)
    enhancement = QgsContrastEnhancement(provider.dataType(1))
    algorithm = _stretch_algorithm()
    if algorithm is not None:
        enhancement.setContrastEnhancementAlgorithm(algorithm, True)
    enhancement.setMinimumValue(0.0)
    enhancement.setMaximumValue(255.0)
    renderer.setContrastEnhancement(enhancement)
    renderer.setOpacity(opacity)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def _hillshade_built_here(path: str, name: str):



    from ..core.postcondition import read_ahead
    from .data_common import built_here, worker_options

    def make():
        layer = QgsRasterLayer(path, name, "gdal", worker_options(QgsRasterLayer))
        if layer.isValid():
            read_ahead(layer)
        return layer

    return built_here(make)


class _HillshadeTask(QgsTask):


    def __init__(self, task_id: str, source: str, output_path: str, layer_name: str, options: dict):
        super().__init__(f"AI Agent hillshade: {layer_name}")
        self.task_id = task_id
        self.source = source
        self.output_path = output_path
        self.layer_name = layer_name
        self.options = options
        self.error = ""
        self.built = None
        self.dem_id = ""

        self.run_token = layer_order.current_run()

    def run(self) -> bool:
        try:
            from osgeo import gdal
        except ImportError as e:
            self.error = f"The osgeo module is not importable: {e}"
            return False


        over_http = self.source.startswith(_REMOTE_PREFIXES)
        try:
            if over_http:
                gdal.SetThreadLocalConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
            self.error = self._shade(gdal)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        finally:
            if over_http:
                try:
                    gdal.SetThreadLocalConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", None)
                except Exception:  # nosec B110
                    pass
        if not self.error and not self.isCanceled():
            self.built = _hillshade_built_here(self.output_path, self.layer_name)
        return not self.error

    def _shade(self, gdal) -> str:

        written = gdal.DEMProcessing(self.output_path, self.source, "hillshade",
                                     options=self._gdaldem_options(gdal))
        if written is None:
            return gdal.GetLastErrorMsg() or "GDAL DEMProcessing produced no output."
        written = None
        if os.path.exists(self.output_path):
            return ""
        return "GDAL reported success but the output file is missing."

    def _gdaldem_options(self, gdal):
        chosen = self.options

        def report(fraction, _message, _data):
            if self.isCanceled():
                return 0
            self.setProgress(min(99.0, float(fraction) * 100.0))
            return 1


        sun = {"multiDirectional": True} if chosen["multidirectional"] else {"azimuth": chosen["azimuth"]}
        return gdal.DEMProcessingOptions(
            format="GTiff",
            band=chosen["band"],
            zFactor=chosen["z_factor"],
            altitude=chosen["altitude"],
            computeEdges=True,
            creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"],
            callback=report,
            **sun,
        )

    def finished(self, result: bool):
        entry = _PROCESSING_TASKS.get(self.task_id)
        if entry is None:
            return
        try:
            if entry.get("status") == "canceled" or self.isCanceled():
                entry["status"] = "canceled"
            elif result:
                with layer_order.adopted(self.run_token):
                    self._add_layer(entry)
            else:
                entry["status"] = "error"
                entry["error"] = self.error or "The hillshade task failed; get_message_log has the GDAL output."
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = f"Output handling failed: {e}"
        entry.pop("task", None)
        built, self.built = self.built, None
        if built is not None:
            built.release()

    def _add_layer(self, entry: dict):
        layer = background.take(self.built)
        if layer is None:
            layer = QgsRasterLayer(self.output_path, self.layer_name, "gdal")
        if not layer.isValid():
            entry["status"] = "error"
            entry["error"] = f"GDAL wrote {self.output_path} but QGIS cannot open it as a raster."
            return
        opacity = self.options["opacity"]
        _style_hillshade(layer, opacity)
        from .processing_guards import HILLSHADE_PROPERTY

        layer.setCustomProperty(HILLSHADE_PROPERTY, self.dem_id or "dem")
        QgsProject.instance().addMapLayer(layer)
        entry["outputs"] = {
            "OUTPUT": {
                "path": self.output_path,
                "layer_name": layer.name(),
                "layer_id": layer.id(),
                "added_to_project": True,
                "style": f"single-band gray 0-255, opacity {opacity}",
            }
        }
        entry["status"] = "complete"
        entry["progress"] = 100
        log(f"Hillshade layer {layer.name()!r} added from {self.output_path}")


def _hillshade_source(args: dict):

    name = args.get("layer_name")
    if name:
        layer = resolve_layer(name)
        if layer is None:
            return None, layer_not_found(name)
    else:
        layer = iface.activeLayer()
        if layer is None:
            return None, tool_error("No active layer.", "INVALID_ARGS", "layer_name names the DEM raster layer.")
    if not isinstance(layer, QgsRasterLayer):
        return None, tool_error(
            f"Layer {layer.name()!r} is not a raster layer.",
            "INVALID_ARGS",
            "layer_name must be a DEM raster (list_layers shows the rasters).",
        )
    if layer.providerType() != "gdal":
        return None, tool_error(
            f"Layer {layer.name()!r} uses the {layer.providerType()} provider; the hillshade needs a GDAL raster.",
            "INVALID_ARGS",
            "a GeoTIFF or COG (add_cog_layer) is needed, not a WMS or XYZ layer.",
        )
    return layer, None


def _number(args: dict, key: str, default: float, low: float, high: float):

    raw = args.get(key)
    if raw is None:
        return default, None
    hint = f"{key} is between {low} and {high}."
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, tool_error(f"{key} must be a number.", "INVALID_ARGS", hint)
    if value < low or value > high:
        return None, tool_error(f"{key} must be between {low} and {high}, got {value}.", "INVALID_ARGS", hint)
    return value, None


def _release_path(path: str):











    from ..core import security
    from .layer_io_tools import _release_layers_at_path

    if security.validate_path(path, write=True, overwrite=True):
        return







    _release_layers_at_path(path, delete_existing=True)


def _create_hillshade(args: dict) -> dict:
    layer, error = _hillshade_source(args)
    if error:
        return error
    from .processing_guards import hillshade_of_hillshade

    refused = hillshade_of_hillshade(layer, "create_hillshade")
    if refused:
        return refused
    values = {}
    for key, default, low, high in (
        ("azimuth", 315.0, 0.0, 360.0),
        ("altitude", 45.0, 0.0, 90.0),
        ("z_factor", 1.0, 1e-9, 1e9),
        ("opacity", 0.6, 0.0, 1.0),
    ):
        value, error = _number(args, key, default, low, high)
        if error:
            return error
        values[key] = value
    band = int(args.get("band") or 1)
    if band < 1 or band > max(1, layer.bandCount()):
        return tool_error(
            f"band must be between 1 and {layer.bandCount()} for {layer.name()!r}.",
            "INVALID_ARGS",
            "band is the DEM band, usually 1.",
        )
    values["band"] = band
    values["multidirectional"] = bool(args.get("multidirectional"))

    source = layer.dataProvider().dataSourceUri() or layer.source()
    name = str(args.get("name") or "").strip() or f"{layer.name()} Hillshade"
    output_path = str(args.get("output_path") or "").strip()
    if output_path:
        from ..core import security



        output_path = os.path.abspath(os.path.expanduser(output_path))
        if not output_path.lower().endswith((".tif", ".tiff")):
            output_path += ".tif"
        problem = security.validate_path(output_path, write=True,
                                         overwrite=bool(args.get("overwrite")))
        if problem:
            return tool_error(problem, "PERMISSION_DENIED",
                              "output_path must sit in the project folder or the user's home; overwrite "
                              "true replaces an existing file.")
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _release_path(output_path)
    else:

        safe = re.sub(r"[^\w-]", "_", name) or "hillshade"




        safe = safe[:60]
        safe = _avoid_reserved_name(safe)
        output_path = os.path.join(create_managed_temp_dir("hillshade"), f"{safe}.tif")

    _sweep_consumed_tasks()
    task_id = "hill-" + uuid.uuid4().hex[:12]
    task = _HillshadeTask(task_id, source, output_path, name, values)
    task.dem_id = layer.id()
    _PROCESSING_TASKS[task_id] = {
        "status": "running",
        "progress": 0,
        "algorithm": "gdal hillshade (create_hillshade)",
        "started_at": time.strftime("%H:%M:%S"),
        "task": task,
        "source_layer": layer.name(),
        "output_path": output_path,
    }
    QgsApplication.taskManager().addTask(task)
    return {
        "task_id": task_id,
        "status": "running",
        "source_layer": layer.name(),
        "source_layer_id": layer.id(),
        "layer_name": name,
        "output_path": output_path,
        "parameters": values,
        "note": "Running in the background, QGIS stays responsive. Poll get_task_status(task_id); "
                "the hillshade layer is added and styled when status is complete.",
    }
