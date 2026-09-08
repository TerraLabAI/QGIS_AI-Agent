# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Why is QGIS slow: a read-only look at the project for the usual causes."""









from __future__ import annotations

from qgis.core import Qgis, QgsProject, QgsRasterLayer, QgsVectorLayer

from ..core.tool_registry import Tool, ToolRegistry
from .layer_lookup import _find_layer, _layer_not_found_error

_MAX_LAYERS = 200
_MAX_FINDINGS = 40
_LARGE_VECTOR = 200_000
_HEAVY_REPROJECT = 50_000
_LARGE_RASTER_PIXELS = 25_000_000
_MANY_VISIBLE = 40
_REMOTE_VECTOR_PROVIDERS = ("WFS", "wfs", "arcgisfeatureserver", "oapif")
_REMOTE_RASTER_PROVIDERS = ("wms", "wcs", "arcgismapserver")
_SEVERITY = {"high": 0, "medium": 1, "low": 2}


def register_diagnostics_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="diagnose_project",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
        },
        handler=_diagnose_project,
    ))


def _finding(layer, severity: str, issue: str, fix: str, tool: str = "") -> dict:
    out = {"layer": layer.name(), "layer_id": layer.id(), "severity": severity, "issue": issue, "fix": fix}
    if tool:
        out["tool"] = tool
    return out


def _vector_findings(layer: QgsVectorLayer, project_crs, visible: bool) -> list[dict]:
    out: list[dict] = []
    provider = layer.providerType()
    source = layer.source()
    remote = provider in _REMOTE_VECTOR_PROVIDERS
    count = -1 if remote else int(layer.featureCount())
    if remote:
        out.append(_finding(layer, "medium", f"remote {provider} layer: every pan asks the server again",
                            "Export it to a GeoPackage for the working session and use the export",
                            "save_layer_to_gpkg"))
    if provider == "virtual":
        out.append(_finding(layer, "medium", "virtual layer: its SQL runs again on every redraw",
                            "Materialise it (save_layer_to_gpkg) once the query is stable", "save_layer_to_gpkg"))
    if provider == "delimitedtext":
        out.append(_finding(layer, "medium", "CSV read live: parsed again at each open, no spatial index possible",
                            "Save it as a GeoPackage layer", "save_layer_to_gpkg"))
    try:
        no_index = layer.hasSpatialIndex() == Qgis.SpatialIndexPresence.NotPresent
    except Exception:  # nosec B110 - QGIS before 3.36 spells the enum differently
        no_index = False
    if no_index and count > 5_000:
        out.append(_finding(layer, "high" if count > _LARGE_VECTOR else "medium",
                            f"no spatial index on {count} features: every zoom scans the whole file",
                            "Create a spatial index (native:createspatialindex on the layer)", "run_processing"))
    if count > _LARGE_VECTOR:
        lower = source.lower()
        if ".shp" in lower:
            out.append(_finding(layer, "high", f"shapefile with {count} features",
                                "Package it to GeoPackage: faster, one file, full field names", "save_layer_to_gpkg"))
        elif provider == "ogr" and ".gpkg" not in lower and not lower.startswith("memory"):
            out.append(_finding(layer, "medium", f"{count} features in {provider}",
                                "GeoPackage draws faster than most file formats", "save_layer_to_gpkg"))
        if visible and not layer.hasScaleBasedVisibility():
            out.append(_finding(layer, "medium", f"{count} features drawn at every scale",
                                "Set a minimum scale so it draws only when zoomed in (set_layer_property min_scale)",
                                "set_layer_property"))
    if count > _HEAVY_REPROJECT and project_crs.isValid() and layer.crs().isValid() \
            and layer.crs().authid() != project_crs.authid():
        out.append(_finding(layer, "low", f"{count} features reprojected on the fly from {layer.crs().authid()} "
                            f"to {project_crs.authid()}",
                            "Reproject the layer once (native:reprojectlayer) or set the project CRS to the data's",
                            "run_processing"))
    return out


def _raster_findings(layer: QgsRasterLayer, visible: bool) -> list[dict]:
    out: list[dict] = []
    provider = layer.providerType()
    if provider in _REMOTE_RASTER_PROVIDERS or provider == "wms":
        return out
    pixels = int(layer.width()) * int(layer.height())
    if pixels > _LARGE_RASTER_PIXELS:
        has_pyramids = False
        try:
            has_pyramids = bool(layer.dataProvider().hasPyramids())
        except Exception:  # nosec B110 - provider without pyramids
            has_pyramids = True
        if not has_pyramids:
            out.append(_finding(layer, "high", f"{pixels // 1_000_000} Mpx raster without overviews: every zoom-out "
                                "reads the full resolution",
                                "Build overviews (gdal:overviews with levels 2 4 8 16)", "run_processing"))
        if visible and not layer.hasScaleBasedVisibility():
            out.append(_finding(layer, "low", "large raster drawn at every scale",
                                "Set a scale range or turn it off while editing", "set_layer_property"))
    return out


def _diagnose_project(args: dict) -> dict:
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    wanted = str(args.get("layer_name") or "").strip()
    layers = list(project.mapLayers().values())
    if wanted:




        found = _find_layer(wanted)
        if found is None:
            return _layer_not_found_error(wanted)
        layers = [found]
    findings: list[dict] = []
    visible_count = 0
    remote_count = 0
    project_crs = project.crs()
    for layer in layers[:_MAX_LAYERS]:
        node = root.findLayer(layer.id())
        visible = bool(node.isVisible()) if node is not None else True
        visible_count += int(visible)
        try:
            if isinstance(layer, QgsVectorLayer):
                if layer.providerType() in _REMOTE_VECTOR_PROVIDERS:
                    remote_count += 1
                findings.extend(_vector_findings(layer, project_crs, visible))
            elif isinstance(layer, QgsRasterLayer):
                if layer.providerType() in _REMOTE_RASTER_PROVIDERS:
                    remote_count += 1
                findings.extend(_raster_findings(layer, visible))
        except Exception as exc:  # noqa: BLE001 - one broken layer must not hide the others
            findings.append({"layer": layer.name(), "layer_id": layer.id(), "severity": "low",
                             "issue": f"could not be inspected: {exc}", "fix": "Check the layer's source"})
    project_findings: list[dict] = []
    if not wanted and visible_count > _MANY_VISIBLE:
        project_findings.append({"severity": "medium", "issue": f"{visible_count} layers visible at once",
                                 "fix": "Group them and hide the groups not in use (set_layers_visibility)",
                                 "tool": "set_layers_visibility"})
    if not wanted and remote_count > 6:
        project_findings.append({"severity": "medium", "issue": f"{remote_count} remote layers (WMS, WFS, tiles)",
                                 "fix": "Turn off the ones not needed for the current task; they wait on the network"})
    findings.sort(key=lambda f: _SEVERITY.get(f["severity"], 3))
    findings = findings[:_MAX_FINDINGS]
    return {
        "layers_checked": min(len(layers), _MAX_LAYERS),
        "layers_visible": visible_count,
        "project_crs": project_crs.authid(),
        "findings": findings,
        "project_findings": project_findings,
        "counts": {level: sum(1 for f in findings if f["severity"] == level) for level in _SEVERITY},
        "note": "Nothing was changed. Each fix names the tool; apply the high ones first and measure again."
        if findings or project_findings else "No usual cause found: if it is still slow, ask which action is slow "
        "(drawing, opening, saving, a tool) and look at the QGIS log.",
    }
