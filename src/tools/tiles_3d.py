# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from qgis.core import QgsDataSourceUri, QgsProject
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

try:

    from qgis.core import QgsTiledSceneLayer
except ImportError:  # pragma: no cover
    QgsTiledSceneLayer = None

from ..core import security
from ..core.tool_registry import Tool, ToolRegistry, tool_error


def register_3d_tiles_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="configure_3d_tiles",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Load 3D Tiles[ from {url}]"),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1, "maxLength": 8192},
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "style": {"type": "string", "enum": ["textured", "wireframe"]},
                "token": {"type": "string", "minLength": 1, "maxLength": 4096},
                "maximum_screen_error": {"type": "number", "minimum": 0.1, "maximum": 1000},
                "show_bounding_boxes": {"type": "boolean"},
                "action": {"type": "string", "enum": ["load", "style", "measure"]},
            },
            "required": ["url"],
        },
        handler=_configure_3d_tiles))


def _with_token(url: str, token: str | None) -> str:
    if not token:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["access_token"] = token
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _scene_uri(url: str) -> str:





    uri = QgsDataSourceUri()
    uri.setParam("url", url)
    return bytes(uri.encodedUri()).decode("utf-8")


def _new_3d_renderer():

    try:
        from qgis._3d import QgsTiledSceneLayer3DRenderer
    except ImportError:
        return None
    return QgsTiledSceneLayer3DRenderer()


def _configure_3d_tiles(args: dict) -> dict:
    url = str(args.get("url") or "").strip()
    if not url or urlsplit(url).scheme not in {"http", "https"}:
        return tool_error("url must be an explicit http(s) 3D Tiles or Cesium Ion tileset URL.", "INVALID_ARGS")
    url_error = security.validate_url(url)
    if url_error:
        return tool_error(url_error, "PERMISSION_DENIED")
    if str(args.get("action") or "load") == "measure":
        return tool_error(
            "QGIS does not expose a stable portable PyQGIS API for measuring in a 3D scene.",
            "CAPABILITY_UNAVAILABLE",
            "Use the native 2D measure_distance tool, or measure manually in the 3D view.")
    if str(args.get("style") or "textured") == "wireframe":
        return tool_error(
            "QGIS's public tiled-scene renderer does not expose a wireframe mode.",
            "CAPABILITY_UNAVAILABLE",
            "Use style='textured', which preserves the material supplied by the tileset.")
    if QgsTiledSceneLayer is None:
        return tool_error("3D Tiles need QGIS 3.34 or later; this QGIS is older.",
                          "CAPABILITY_UNAVAILABLE",
                          "Update QGIS, or add the tileset as a plain layer instead.")
    layer = QgsTiledSceneLayer(
        _scene_uri(_with_token(url, str(args.get("token") or "").strip() or None)),
        str(args.get("name") or "3D Tiles"), "cesiumtiles")
    if not layer.isValid():
        detail = layer.error().summary() or "the provider rejected the tileset or could not reach it"
        return tool_error(f"QGIS could not load the 3D Tiles source: {detail}.", "EXECUTION_FAILED",
                          "Check the explicitly supplied URL and token, then retry.")



    renderer = layer.renderer3D() or _new_3d_renderer()
    if renderer is not None:
        if args.get("maximum_screen_error") is not None:
            renderer.setMaximumScreenError(float(args["maximum_screen_error"]))
        if args.get("show_bounding_boxes") is not None:
            renderer.setShowBoundingBoxes(bool(args["show_bounding_boxes"]))
        layer.setRenderer3D(renderer)
    QgsProject.instance().addMapLayer(layer)
    result = {"layer_name": layer.name(), "layer_id": layer.id(), "provider": "cesiumtiles", "style": "textured"}
    if renderer is None:
        result["note"] = "This QGIS has no 3D library: the tileset draws on the 2D map only."
    if args.get("maximum_screen_error") is not None:
        result["maximum_screen_error"] = float(args["maximum_screen_error"])
    if args.get("show_bounding_boxes") is not None:
        result["show_bounding_boxes"] = bool(args["show_bounding_boxes"])
    return result
