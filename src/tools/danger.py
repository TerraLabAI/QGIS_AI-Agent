# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Danger level of every tool, decided here and nowhere else."""












from __future__ import annotations

import os
import re
from typing import Any

DEFAULT_DANGER = "write"

DANGER: dict[str, str] = {

    "get_project_context": "read",
    "get_project_info": "read",
    "list_layers": "read",
    "get_layer_info": "read",
    "add_vector_layer": "write",
    "add_raster_layer": "write",
    "remove_layer": "destructive",
    "zoom_to_layer": "write",
    "get_features": "read",
    "run_processing": "write",
    "get_task_status": "read",
    "cancel_task": "write",
    "list_tasks": "read",
    "evaluate_expression": "read",
    "get_field_statistics": "read",
    "get_renderer_info": "read",
    "raster_sample": "read",
    "get_raster_band_stats": "read",
    "get_provider_capabilities": "read",
    "check_geometry_validity": "read",
    "batch_commands": "write",
    "set_layer_style": "write",
    "take_screenshot": "read",
    "list_algorithms": "read",
    "get_algorithm_help": "read",
    "set_layer_labels": "write",
    "export_layer": "destructive",
    "add_field": "write",

    "add_features": "write",
    "update_features": "write",
    "delete_features": "destructive",
    "select_features": "write",
    "get_selection": "read",
    "clear_selection": "write",
    "select_by_attribute": "write",
    "select_by_geometry": "write",


    "qgis_edit_begin": "write",
    "qgis_edit_commit": "write",
    "qgis_edit_rollback": "write",
    "qgis_edit_restore_aids": "write",
    "qgis_arm_tool": "write",
    "qgis_move_vertex": "write",
    "qgis_split_feature": "write",
    "qgis_reshape_feature": "write",
    "qgis_edit_state": "read",
    "qgis_add_vertex": "write",
    "qgis_delete_vertex": "write",
    "qgis_translate_feature": "write",
    "qgis_rotate_feature": "write",
    "qgis_simplify_feature": "write",
    "qgis_merge_features": "write",
    "qgis_add_ring": "write",
    "qgis_add_part": "write",
    "qgis_undo": "write",
    "qgis_redo": "write",

    "set_active_layer": "write",
    "get_active_layer": "read",
    "set_layer_visibility": "write",
    "set_layers_visibility": "write",
    "set_layer_metadata": "write",
    "set_field_aliases": "write",
    "create_memory_layer": "write",
    "save_layer_to_gpkg": "destructive",
    "get_layer_tree": "read",
    "create_layer_group": "write",
    "move_layer_to_group": "write",
    "transform_coordinates": "read",
    "get_canvas_extent": "read",
    "set_canvas_extent": "write",
    "set_project_crs": "write",
    "save_project": "destructive",
    "load_project": "destructive",
    "create_new_project": "destructive",

    "create_print_layout": "write",
    "add_layout_map": "write",
    "add_layout_label": "write",
    "add_layout_legend": "write",
    "add_layout_scalebar": "write",
    "add_layout_north_arrow": "write",
    "get_layout_info": "read",
    "remove_print_layout": "destructive",

    "execute_code": "destructive",
    "render_map": "read",
    "render_detection_reveal": "destructive",
    "render_camera_move": "destructive",
    "list_layouts": "read",
    "export_layout": "destructive",
    "get_message_log": "read",
    "get_debug_info": "read",

    "geocode": "read",
    "reverse_geocode": "read",
    "fetch_osm_data": "write",
    "fetch_building_footprints": "write",
    "fetch_overture": "write",
    "list_xyz_sources": "read",
    "add_xyz_layer": "write",
    "add_wms_layer": "write",
    "add_wfs_layer": "write",
    "add_vector_from_url": "write",
    "get_route": "write",
    "measure_distance": "read",
    "inspect_data_source": "read",
    "search_open_data": "read",

    "fetch_json": "read",
    "fetch_text": "read",
    "diagnose_project": "read",
    "add_points_from_json": "write",

    "repair_layer_paths": "write",
    "package_project": "destructive",
    "get_isochrone": "write",
    "delineate_watershed": "write",
    "elevation_profile": "read",
    "check_topology": "read",
    "geocode_layer": "write",
    "get_geocode_layer_status": "read",

    "create_grid_layer": "write",
    "encode_cells": "write",
    "decode_cell": "read",

    "add_cog_layer": "write",
    "add_stac_layer": "write",
    "add_pmtiles_layer": "write",
    "map_statistic": "write",

    "initialize_earth_engine": "read",
    "add_gee_dataset": "write",
    "gee_compute_index": "write",
    "gee_zonal_stats": "read",

    "check_optional_dependencies": "read",
    "install_dependency": "destructive",
    "install_dependency_status": "read",


    "list_plugins": "read",
    "get_plugin_info": "read",
    "trigger_plugin_action": "destructive",
    "trigger_menu_action": "destructive",
    "reload_plugin": "destructive",
    "get_tool_result": "read",

    "ai_edit_status": "read",
    "ai_edit_get_presets": "read",
    "ai_edit_get_credits": "read",
    "ai_edit_get_resolutions": "read",
    "ai_edit_cancel": "write",
    "ai_edit_vectorize": "destructive",
    "ai_edit_markup": "write",
    "ai_edit_select_version": "write",
    "ai_edit_generate": "destructive",

    "ai_segment_status": "read",
    "ai_segment_load_model": "write",
    "ai_segment_get_presets": "read",
    "ai_segment_set_mode": "write",
    "ai_segment_set_zone": "write",
    "ai_segment_detect_auto": "destructive",
    "ai_segment_review_filter": "write",
    "ai_segment_set_display_mode": "write",
    "ai_segment_auto_status": "read",
    "ai_segment_auto_cancel": "write",







    "remember": "write",


    "forget": "write",

    "ai_segment_install_status": "read",

    "ai_edit_debug_state": "read",
    "ai_edit_signout": "write",
    "ai_edit_set_key": "write",
    "ai_edit_mock_usage": "write",
    "ai_edit_restore_usage": "write",
    "ai_edit_set_dev_flags": "write",
    "ai_edit_simulate_signup": "write",
    "ai_edit_run_generation": "destructive",
    "ai_edit_generation_status": "read",
    "ai_edit_attach_reference": "write",
    "ai_edit_set_resolution": "write",
    "ai_edit_set_model": "write",
    "ai_edit_clear_references": "write",
    "ai_edit_backend_new_free_key": "destructive",

    "execute_sql": "write",
    "zonal_statistics": "write",
    "spatial_join": "write",








    "raster_calculator": "write",
    "field_calculator": "write",
    "get_unique_values": "read",
    "identify_features": "read",
    "validate_expression": "read",
    "get_layer_extent": "read",
    "find_processing_algorithm": "read",

    "set_raster_style": "write",
    "apply_style_qml": "write",
    "save_style_qml": "destructive",
    "set_layer_property": "write",
    "set_layer_order": "write",
    "set_layer_crs": "write",
    "get_layer_crs": "read",
    "delete_field": "destructive",
    "rename_field": "write",
    "add_table_join": "write",
    "duplicate_layer": "write",

    "list_connections": "read",
    "list_connection_tables": "read",
    "add_layer_from_connection": "write",
    "create_postgresql_connection": "write",
    "get_bookmarks": "read",
    "add_bookmark": "write",
    "remove_bookmark": "destructive",
    "get_map_themes": "read",
    "add_map_theme": "write",
    "apply_map_theme": "write",
    "remove_map_theme": "destructive",
    "get_project_variables": "read",
    "set_project_variable": "write",
    "get_setting": "read",
    "set_setting": "destructive",

    "add_layout_picture": "write",
    "add_layout_table": "write",
    "configure_atlas": "write",
    "export_atlas": "destructive",
    "get_3d_screenshot": "read",

    "get_processing_providers": "read",
    "execute_processing_batch": "write",
    "create_processing_model": "destructive",
    "list_processing_models": "read",
    "run_model": "write",

    "get_layer_labeling": "read",
    "get_canvas_scale": "read",
    "set_canvas_scale": "write",
    "update_feature_geometry": "write",

    "set_layer_filter": "write",
    "zoom_to_selected": "write",
    "open_attribute_table": "write",
    "create_hillshade": "write",

    "add_arcgis_rest_layer": "write",
    "get_stac_item_assets": "read",

    "find_local_data": "read",

    "add_data": "write",
    "ai_edit": "write",
    "ai_segment": "write",
    "ask_user": "read",










    "accessibility_snapshot": "read",
    "ref_action": "destructive",
    "assert_ui": "read",
    "close_dialog": "write",
    "select_menu_item": "destructive",
    "press_key": "destructive",
    "drag_canvas": "destructive",
    "click_canvas": "destructive",
    "wait_for_log": "read",
    "get_plugin_debug_context": "read",
    "get_network_log": "read",
    "clear_network_log": "write",
    "get_python_errors": "read",
    "clear_python_errors": "write",

    "reset_plugin_fixture": "destructive",
    "run_plugin_flow": "destructive",
    "take_qgis_window_screenshot": "read",
    "take_widget_screenshot": "read",
    "open_plugin_panel": "write",
}


AI_EDIT_ACTION_DANGER = {
    "status": "read",
    "generation_status": "read",
    "presets": "read",
    "select_version": "write",
    "cancel": "write",
    "generate": "destructive",
    "vectorize": "destructive",
}
AI_SEGMENT_ACTION_DANGER = {
    "status": "read",
    "auto_status": "read",
    "presets": "read",
    "install_status": "read",
    "set_zone": "write",
    "cancel": "write",
    "detect_auto": "destructive",
}


_OPTIONAL_PATH_ARGS = {
    "take_screenshot": ("save_path",),
    "render_map": ("save_path", "output_path"),
    "take_qgis_window_screenshot": ("save_path",),
    "take_widget_screenshot": ("save_path",),
    "ref_action": ("save_path",),
    "create_memory_layer": ("gpkg_path",),
    "create_hillshade": ("output_path",),
    "get_3d_screenshot": ("save_path",),
    "zonal_statistics": ("output_path",),
    "spatial_join": ("output_path",),
    "raster_calculator": ("output_path",),
}


_APPENDS_EXTENSION = {"create_hillshade": ".tif"}





_CORE_PROVIDERS = frozenset({
    "native", "qgis", "gdal", "grass", "grass7", "saga", "otb", "3d",
    "model", "script", "pdal", "project",
})













_INERT_ALGORITHMS = frozenset({
    "quickosm:buildqueryaroundarea",
    "quickosm:buildquerybyattributeonly",
    "quickosm:buildqueryextent",
    "quickosm:buildqueryinsidearea",
    "quickosm:buildrawquery",
    "terraedit:editstatus",
    "terralab:segmentationstatus",
})






PAID_ALGORITHMS = frozenset({
    "terraedit:generate",
    "terraedit:vectorize",
    "terralab:segmentpoint",
    "terralab:segmentzone",
})




_OFF_MACHINE_PROVIDERS = ("ORS Tools:",)


_RANK = {"read": 0, "write": 1, "destructive": 2}
_SQL_MUTATION_RE = re.compile(r"(?is)^\s*(?:with\b.*?)?\b(?:delete|drop|truncate|update|alter|insert|replace|create)\b")
_MEMORY_SINKS = ("memory:", "ogr:", "postgres:", "TEMPORARY_OUTPUT")
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")



_OGR_DBNAME_RE = re.compile(r"^ogr:dbname='(?P<path>[^']+)'")


def static_danger(name: str) -> str:
    return DANGER.get(name, DEFAULT_DANGER)


def looks_like_disk_path(value: Any) -> bool:
    """A string with a directory separator or drive letter and a file extension."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    ogr_dbname = _OGR_DBNAME_RE.match(text)
    if ogr_dbname:
        text = ogr_dbname.group("path")
    elif not text or text in _MEMORY_SINKS or text.startswith(_MEMORY_SINKS):
        return False
    if text.startswith(("http://", "https://", "/vsicurl/", "ftp://")):
        return False
    has_dir = "/" in text or "\\" in text or bool(_DRIVE_RE.match(text)) or text.startswith("~")


    file_part = text.split("|", 1)[0]





    extension = os.path.splitext(file_part)[1]
    has_ext = len(extension) > 1 and extension[1:].replace("_", "").isalnum()
    return has_dir and has_ext


def _processing_output_params(algorithm_id: str) -> set[str] | None:
    """Names of the destination parameters, from QGIS when it is importable."""
    try:
        from qgis.core import QgsApplication

        alg = QgsApplication.processingRegistry().algorithmById(algorithm_id)
        if alg is None:
            return None
        return {
            d.name()
            for d in alg.parameterDefinitions()
            if getattr(d, "isDestination", lambda: False)()
        }
    except Exception:
        return None


def _processing_writes_to_disk(args: dict) -> bool:
    parameters = args.get("parameters") if isinstance(args, dict) else None
    if not isinstance(parameters, dict):
        return False
    outputs = _processing_output_params(str(args.get("algorithm_id") or ""))
    for key, value in parameters.items():
        if outputs is not None:
            is_output = key in outputs
        else:
            upper = key.upper()
            is_output = "OUTPUT" in upper or "DEST" in upper
        if not is_output:
            continue
        candidate = value.get("path") if isinstance(value, dict) else value
        if looks_like_disk_path(candidate):
            return True
    return False


def plugin_algorithm_danger(algorithm_id: str) -> str | None:
    """``destructive`` when a plugin algorithm's effect is not in its parameters."""





    algorithm_id = str(algorithm_id or "").strip()
    if not algorithm_id:
        return None
    if algorithm_id in PAID_ALGORITHMS or algorithm_id.startswith(_OFF_MACHINE_PROVIDERS):
        return "destructive"
    if algorithm_id in _INERT_ALGORITHMS or ":" not in algorithm_id:
        return None
    provider = algorithm_id.split(":", 1)[0]
    if provider.lower() in _CORE_PROVIDERS:
        return None
    outputs = _processing_output_params(algorithm_id)


    if outputs is None or outputs:
        return None
    return "destructive"


def effective_danger(name: str, args: dict | None = None) -> str:
    """The level to enforce for one call."""












    args = args if isinstance(args, dict) else {}
    level = static_danger(name)
    if name == "ai_edit":
        return AI_EDIT_ACTION_DANGER.get(str(args.get("action") or ""), level)
    if name == "ai_segment":
        return AI_SEGMENT_ACTION_DANGER.get(str(args.get("action") or ""), level)



    if name == "trigger_plugin_action" and not str(args.get("action_path") or "").strip():
        return "read"
    if name == "trigger_menu_action" and (
        args.get("list_only") or not str(args.get("action_path") or "").strip()
    ):
        return "read"
    if name == "run_processing":
        by_algorithm = plugin_algorithm_danger(str(args.get("algorithm_id") or ""))
        if by_algorithm or _processing_writes_to_disk(args):
            return "destructive"
    if name == "execute_processing_batch":
        algorithm = str(args.get("algorithm") or "")
        if plugin_algorithm_danger(algorithm):
            return "destructive"
        for parameters in args.get("parameters_list") or []:
            if _processing_writes_to_disk({"algorithm_id": algorithm, "parameters": parameters}):
                return "destructive"
        return level
    if name == "run_model":
        model = str(args.get("model") or "")
        if plugin_algorithm_danger(model) or _processing_writes_to_disk(
            {"algorithm_id": model, "parameters": args.get("parameters")}
        ):
            return "destructive"
    if name == "execute_sql" and _SQL_MUTATION_RE.search(str(args.get("query") or args.get("sql") or "")):
        return "destructive"





    if name == "add_layer_from_connection" and str(args.get("sql") or "").strip():
        return "destructive"
    if name == "batch_commands":
        for command in args.get("commands") or []:
            if not isinstance(command, dict):
                continue
            inner = effective_danger(str(command.get("name") or ""), command.get("arguments") or {})
            if _RANK[inner] > _RANK[level]:
                level = inner
        return level
    for arg in _OPTIONAL_PATH_ARGS.get(name, ()):
        value = args.get(arg)





        if isinstance(value, str) and name in _APPENDS_EXTENSION and not os.path.splitext(value)[1]:
            value = value + _APPENDS_EXTENSION[name]
        if looks_like_disk_path(value):
            return "destructive"
    return level


def unclassified(names) -> list[str]:
    """Registered tool names that fall back to the default level."""
    return sorted(n for n in names if n not in DANGER)
