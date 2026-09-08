# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Third harvest batch, view side: filter a layer in place, zoom to the selection, open the attribute table, hillshade a DEM in the background."""












from __future__ import annotations

import os
import time
import uuid

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsContrastEnhancement,
    QgsFeatureRequest,
    QgsProject,
    QgsRasterLayer,
    QgsSingleBandGrayRenderer,
    QgsTask,
    QgsVectorLayer,
)
from qgis.utils import iface

from ..core.logger import log
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import core_tools as _core
from ._layers import layer_not_found, resolve_layer
from .data_tools import _avoid_reserved_name

_REMOTE_PREFIXES = ("/vsicurl/", "http://", "https://", "/vsis3/", "/vsiaz/", "/vsigs/")


def register_harvest_view_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="set_layer_filter",
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
    """(layer, None) for a vector layer, (None, error dict) otherwise."""
    layer = resolve_layer(name_or_id)
    if layer is None:
        return None, layer_not_found(name_or_id)
    if not isinstance(layer, QgsVectorLayer):
        return None, tool_error(
            f"Layer {layer.name()!r} is not a vector layer.",
            "INVALID_ARGS",
            "Pick a vector layer from list_layers.",
        )
    return layer, None


def _feature_count(layer) -> int:
    """featureCount(), or a count by iteration when the provider says unknown (-1)."""
    count = layer.featureCount()
    if count is not None and count >= 0:
        return int(count)



    request = QgsFeatureRequest()
    request.setNoAttributes()
    request.setLimit(_COUNT_CAP)
    try:
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    except Exception:  # nosec B110 - render hint cleanup is optional
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
            "Commit or roll back the edits first (qgis_edit_commit / qgis_edit_rollback), then call "
            "set_layer_filter again.",
        )
    previous = layer.subsetString() or ""
    if not layer.setSubsetString(expression):
        return tool_error(
            f"The provider rejected the filter {expression!r} on {layer.name()!r}.",
            "INVALID_ARGS",
            "Check the field names with get_layer_info. OGR, GeoPackage and PostgreSQL layers take SQL "
            "WHERE syntax (double quotes around field names, single quotes around text); memory layers "
            "take a QGIS expression. validate_expression can check the expression first.",
        )
    layer.triggerRepaint()
    count = _feature_count(layer)
    out = {
        "layer_id": layer.id(),
        "layer_name": layer.name(),
        "filter": layer.subsetString() or "",
        "previous_filter": previous,
        "cleared": expression == "",
        "feature_count": count,
    }
    if expression and count == 0:
        out["_note"] = "The filter matches no feature: the layer shows nothing until the filter changes."
    return out





def _zoom_to_selected(args: dict) -> dict:
    name = args.get("layer_name")
    if name:
        layer, error = _vector_layer_or_error(name)
        if error:
            return error
    else:
        layer = iface.activeLayer()
        if layer is None:
            return tool_error("No active layer.", "INVALID_ARGS", "Pass layer_name, or set_active_layer first.")
        if not isinstance(layer, QgsVectorLayer):
            return tool_error(
                f"The active layer {layer.name()!r} is not a vector layer.",
                "INVALID_ARGS",
                "Pass layer_name with a vector layer that has a selection.",
            )
    selected = layer.selectedFeatureCount()
    if selected == 0:
        return tool_error(
            f"Layer {layer.name()!r} has no selected features.",
            "INVALID_ARGS",
            "Select features first with select_by_attribute, select_by_geometry or select_features.",
        )
    canvas = iface.mapCanvas()
    canvas.zoomToSelected(layer)
    canvas.refresh()
    return {
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
            "Check get_layer_info: the layer may be invalid. accessibility_snapshot shows what is on screen.",
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
    """Single-band gray, 0 to 255, at the given opacity: the classic hillshade look."""
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


class _HillshadeTask(QgsTask):
    """GDAL DEMProcessing in the worker; the layer is made in finished() on the main thread."""

    def __init__(self, task_id: str, source: str, output_path: str, layer_name: str, options: dict):
        super().__init__(f"AI Agent hillshade: {layer_name}")
        self.task_id = task_id
        self.source = source
        self.output_path = output_path
        self.layer_name = layer_name
        self.options = options
        self.error = ""

    def run(self) -> bool:
        try:
            from osgeo import gdal
        except ImportError as e:
            self.error = f"The osgeo module is not importable: {e}"
            return False
        remote = self.source.startswith(_REMOTE_PREFIXES)
        try:
            if remote:

                gdal.SetThreadLocalConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

            def _progress(complete, _message, _data):
                if self.isCanceled():
                    return 0
                self.setProgress(min(99.0, float(complete) * 100.0))
                return 1

            settings = {
                "altitude": self.options["altitude"],
                "zFactor": self.options["z_factor"],
                "band": self.options["band"],
                "computeEdges": True,
                "format": "GTiff",
                "creationOptions": ["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"],
                "callback": _progress,
            }

            if self.options["multidirectional"]:
                settings["multiDirectional"] = True
            else:
                settings["azimuth"] = self.options["azimuth"]
            options = gdal.DEMProcessingOptions(**settings)
            dataset = gdal.DEMProcessing(self.output_path, self.source, "hillshade", options=options)
            if dataset is None:
                self.error = gdal.GetLastErrorMsg() or "GDAL DEMProcessing produced no output."
                return False
            dataset = None
            if not os.path.exists(self.output_path):
                self.error = "GDAL reported success but the output file is missing."
                return False
            return True
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            return False
        finally:
            if remote:
                try:
                    gdal.SetThreadLocalConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", None)
                except Exception:  # nosec B110 - render hint cleanup is optional
                    pass

    def finished(self, result: bool):
        entry = _core._PROCESSING_TASKS.get(self.task_id)
        if entry is None:
            return
        try:
            if entry.get("status") == "canceled" or self.isCanceled():
                entry["status"] = "canceled"
            elif result:
                self._add_layer(entry)
            else:
                entry["status"] = "error"
                entry["error"] = self.error or "The hillshade task failed; get_message_log has the GDAL output."
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = f"Output handling failed: {e}"
        entry.pop("task", None)

    def _add_layer(self, entry: dict):
        layer = QgsRasterLayer(self.output_path, self.layer_name, "gdal")
        if not layer.isValid():
            entry["status"] = "error"
            entry["error"] = f"GDAL wrote {self.output_path} but QGIS cannot open it as a raster."
            return
        opacity = self.options["opacity"]
        _style_hillshade(layer, opacity)
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
    """(layer, None) for a GDAL raster to shade, (None, error) otherwise."""
    name = args.get("layer_name")
    if name:
        layer = resolve_layer(name)
        if layer is None:
            return None, layer_not_found(name)
    else:
        layer = iface.activeLayer()
        if layer is None:
            return None, tool_error("No active layer.", "INVALID_ARGS", "Pass layer_name with the DEM raster layer.")
    if not isinstance(layer, QgsRasterLayer):
        return None, tool_error(
            f"Layer {layer.name()!r} is not a raster layer.",
            "INVALID_ARGS",
            "Pass layer_name with a DEM raster (list_layers shows the rasters).",
        )
    if layer.providerType() != "gdal":
        return None, tool_error(
            f"Layer {layer.name()!r} uses the {layer.providerType()} provider; the hillshade needs a GDAL raster.",
            "INVALID_ARGS",
            "Use a GeoTIFF or a Cloud-Optimized GeoTIFF (add_cog_layer), not a WMS or XYZ layer.",
        )
    return layer, None


def _number(args: dict, key: str, default: float, low: float, high: float):
    """(value, None) inside [low, high], (None, error) otherwise."""
    raw = args.get(key)
    if raw is None:
        return default, None
    hint = f"Pass {key} between {low} and {high}."
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, tool_error(f"{key} must be a number.", "INVALID_ARGS", hint)
    if value < low or value > high:
        return None, tool_error(f"{key} must be between {low} and {high}, got {value}.", "INVALID_ARGS", hint)
    return value, None


def _release_path(path: str):
    """Drop project layers that read ``path`` and delete the file, so GDAL can rewrite it."""










    from ..core import security
    from .layer_io_tools import _release_layers_at_path

    if security.validate_path(path, write=True, overwrite=True):
        return







    _release_layers_at_path(path, delete_existing=True)


def _create_hillshade(args: dict) -> dict:
    layer, error = _hillshade_source(args)
    if error:
        return error
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
            "Pass the DEM band number, usually 1.",
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
                              "Pick a path inside the project folder or the user's home, and pass "
                              "overwrite true to replace a file that already exists.")
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _release_path(output_path)
    else:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name) or "hillshade"




        safe = safe[:60]
        safe = _avoid_reserved_name(safe)
        output_path = os.path.join(create_managed_temp_dir("hillshade"), f"{safe}.tif")

    _core._sweep_consumed_tasks()
    task_id = "hill-" + uuid.uuid4().hex[:12]
    task = _HillshadeTask(task_id, source, output_path, name, values)
    _core._PROCESSING_TASKS[task_id] = {
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
