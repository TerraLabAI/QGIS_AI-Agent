# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the agent is wired into inside QGIS, grouped the way a GIS person thinks."""
















from __future__ import annotations

from typing import Any






_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "id": "project",
        "glyph": "layers",
        "name": "Project and layers",
        "note": "Open and save projects, read and rearrange the layer tree, set the CRS.",
        "prefixes": ("add_layout_",),
        "members": (
            "create_new_project", "load_project", "save_project", "package_project",
            "get_project_info", "get_project_context", "get_project_variables",
            "set_project_variable", "set_project_crs", "diagnose_project", "repair_layer_paths",
            "get_layer_tree", "list_layers", "create_layer_group", "move_layer_to_group",
            "set_layer_order", "set_layer_visibility", "set_layers_visibility", "remove_layer",
            "duplicate_layer", "get_active_layer", "set_active_layer", "set_layer_property",
            "set_layer_metadata", "set_layer_crs", "get_layer_crs", "get_layer_extent",
            "get_layer_info", "add_bookmark", "get_bookmarks", "remove_bookmark",
            "add_map_theme", "get_map_themes", "apply_map_theme", "remove_map_theme",
            "create_memory_layer", "save_layer_to_gpkg", "export_layer", "find_local_data",
            "get_setting", "set_setting", "get_message_log", "get_debug_info", "remember", "forget",
            "ask_user", "get_tool_result", "batch_commands",
        ),
    },
    {
        "id": "add_data",
        "glyph": "globe",
        "name": "Adding data",
        "note": "Every format and service QGIS reads: files, OGC services, tiles, STAC, databases.",
        "prefixes": (),
        "members": (
            "add_data", "add_vector_layer", "add_raster_layer", "add_vector_from_url",
            "add_wms_layer", "add_wfs_layer", "add_xyz_layer", "add_arcgis_rest_layer",
            "add_cog_layer", "add_pmtiles_layer", "add_stac_layer", "add_points_from_json",
            "add_gee_dataset", "add_layer_from_connection", "list_xyz_sources",
            "inspect_data_source", "list_connections", "list_connection_tables",
            "create_postgresql_connection", "get_provider_capabilities", "execute_sql",
            "search_open_data", "fetch_osm_data", "fetch_building_footprints", "fetch_overture",
            "get_stac_item_assets", "initialize_earth_engine",
            "fetch_json", "fetch_text",
        ),
    },
    {
        "id": "read",
        "glyph": "search",
        "name": "Reading and querying",
        "note": "Features, fields, statistics, expressions and selections, without changing anything.",
        "prefixes": (),
        "members": (
            "get_features", "get_field_statistics", "get_unique_values", "get_selection",
            "identify_features", "select_features", "select_by_attribute", "select_by_geometry",
            "clear_selection", "open_attribute_table", "evaluate_expression", "validate_expression",
            "raster_sample", "get_raster_band_stats", "check_geometry_validity", "check_topology",
            "measure_distance", "transform_coordinates", "elevation_profile", "delineate_watershed",
        ),
    },
    {
        "id": "edit",
        "glyph": "pencil",
        "name": "Editing",
        "note": "Geometry down to the vertex, attributes, fields, with the edit buffer and undo.",
        "prefixes": ("qgis_",),
        "members": (
            "add_features", "update_features", "update_feature_geometry", "delete_features",
            "add_field", "delete_field", "rename_field", "set_field_aliases", "field_calculator",
            "add_table_join", "geocode_layer", "get_geocode_layer_status",
        ),
    },
    {
        "id": "processing",
        "glyph": "spark",
        "name": "Processing",
        "note": "The whole Processing toolbox, its models, and long jobs that keep QGIS responsive.",
        "prefixes": (),
        "members": (
            "list_algorithms", "find_processing_algorithm", "get_algorithm_help", "run_processing",
            "execute_processing_batch", "get_processing_providers", "create_processing_model",
            "list_processing_models", "run_model", "get_task_status", "list_tasks", "cancel_task",
            "spatial_join", "zonal_statistics", "map_statistic", "raster_calculator", "create_hillshade",
            "create_grid_layer", "encode_cells", "decode_cell", "gee_compute_index",
            "gee_zonal_stats", "execute_code",
        ),
    },
    {
        "id": "style",
        "glyph": "classify",
        "name": "Styling and labels",
        "note": "Renderers, colour ramps, QML styles, labelling and filters.",
        "prefixes": (),
        "members": (
            "set_layer_style", "get_renderer_info", "set_raster_style", "apply_style_qml",
            "save_style_qml", "set_layer_labels", "get_layer_labeling", "set_layer_filter",
        ),
    },
    {
        "id": "layout",
        "glyph": "layout",
        "name": "Print layouts and atlas",
        "note": "Build a layout, place its elements, drive an atlas, export to PDF or image.",
        "prefixes": (),
        "members": (
            "create_print_layout", "list_layouts", "get_layout_info", "remove_print_layout",
            "configure_atlas", "export_atlas", "export_layout",
        ),
    },
    {
        "id": "view",
        "glyph": "eye",
        "name": "The map view",
        "note": "Move the canvas, zoom, render an image, take a screenshot of what you see.",
        "prefixes": ("render_",),
        "members": (
            "get_canvas_extent", "set_canvas_extent", "get_canvas_scale", "set_canvas_scale",
            "zoom_to_layer", "zoom_to_selected", "take_screenshot", "get_3d_screenshot",
        ),
    },
    {
        "id": "places",
        "glyph": "pin",
        "name": "Places and routes",
        "note": "Turn an address into a point, a point into an address, and points into a route.",
        "prefixes": (),
        "members": ("geocode", "reverse_geocode", "get_route", "get_isochrone"),
    },
    {
        "id": "plugins",
        "glyph": "package",
        "name": "Plugins and menus",
        "note": "Read what is installed and fire any plugin's menu action, AI Edit and "
                "AI Segmentation included, or a QGIS menu action.",






        "prefixes": ("install_dependency", "ai_edit", "ai_segment"),
        "members": (
            "list_plugins", "get_plugin_info", "reload_plugin", "trigger_plugin_action",
            "trigger_menu_action", "check_optional_dependencies",
        ),
    },
)





_DEVELOPER: frozenset[str] = frozenset({
    "ai_edit_backend_new_free_key", "ai_edit_debug_state", "ai_edit_mock_usage",
    "ai_edit_restore_usage", "ai_edit_set_dev_flags", "ai_edit_simulate_signup",
    "get_debug_info", "get_message_log",
})


def _matches(name: str, group: dict[str, Any]) -> bool:
    if name in group["members"]:
        return True
    return any(name.startswith(prefix) for prefix in group["prefixes"])


def _names(registry: Any) -> list[str]:
    """Tool names of a registry, or of a plain iterable of names in a test."""
    try:
        names = [str(n) for n in registry.tool_names]
    except AttributeError:
        names = [str(n) for n in (registry or [])]
    return [n for n in names if n not in _DEVELOPER]


def capability_groups(registry: Any) -> list[dict[str, Any]]:
    """The "Inside QGIS" rows: the authored label, the live count."""




    names = _names(registry)
    out: list[dict[str, Any]] = []
    for group in _GROUPS:
        count = sum(1 for name in names if _matches(name, group))
        if not count:
            continue
        out.append({
            "id": group["id"],
            "name": group["name"],
            "note": group["note"],
            "glyph": group["glyph"],
            "count": count,
        })
    return out


def unassigned(registry: Any) -> list[str]:
    """Registered tools no group claims."""




    names = _names(registry)
    return sorted(n for n in names if not any(_matches(n, g) for g in _GROUPS))


def counted(registry: Any) -> list[str]:
    """The tool names the page counts: everything registered, minus ``_DEVELOPER``."""
    return sorted(_names(registry))
