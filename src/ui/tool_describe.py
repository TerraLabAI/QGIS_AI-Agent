# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One plain line for a tool call, in the words a GIS user reads."""





















from __future__ import annotations

import os
import re

from qgis.PyQt.QtCore import QCoreApplication

from .card_base import humanise_tool_name



TR_CONTEXT = "AIAgent"


def tr(text: str, disambiguation: str | None = None, n: int = -1) -> str:
    """Translate a string that lives outside a QObject method."""
    return QCoreApplication.translate(TR_CONTEXT, text, disambiguation, n)


_LAYER_KEYS = ("layer_name", "layer", "name", "target_layer", "polygon_layer", "input")
_MAX_VALUE = 48



_TEMPLATES = {

    "get_project_context": "Read the project",
    "get_project_info": "Read the project details",
    "list_layers": "List the layers",
    "get_layer_tree": "Read the layer tree",
    "get_layer_info": "Inspect {layer_name}",
    "get_layer_extent": "Read the extent of {layer_name}",
    "get_layer_crs": "Read the CRS of {layer_name}",
    "get_renderer_info": "Read the style of {layer_name}",
    "get_layer_labeling": "Read the labels of {layer_name}",
    "add_vector_layer": "Add {path}[ as {name}]",
    "add_raster_layer": "Add {path}[ as {name}]",
    "add_vector_from_url": "Add {url}[ as {layer_name}]",
    "add_data": "Add {source}[ as {name}]",
    "add_layer_from_connection": "Add {table} from {connection}",
    "create_memory_layer": "Create the layer {name}",
    "duplicate_layer": "Duplicate {layer_name}[ as {new_name}]",
    "remove_layer": "Remove {layer_name}",
    "set_active_layer": "Select {layer_name} in the layer panel",
    "set_layer_visibility": "Show or hide {layer_name}",
    "set_layer_order": "Reorder the layers",
    "set_layer_crs": "Set the CRS of {layer_name} to {crs}",
    "set_layer_filter": "Filter {layer_name}[: {filter}]",
    "set_layer_property": "Set {property} on {layer_name}",
    "create_layer_group": "Create the group {name}",
    "move_layer_to_group": "Move {layer_name} to {group_name}",
    "save_layer_to_gpkg": "Save {layer} to {gpkg_path}",
    "export_layer": "Export {layer_name} to {path}",
    "zoom_to_layer": "Zoom to {layer_name}",
    "zoom_to_selected": "Zoom to the selection[ of {layer_name}]",
    "set_canvas_extent": "Move the map view",
    "get_canvas_extent": "Read the map view",
    "set_canvas_scale": "Set the map scale",
    "get_canvas_scale": "Read the map scale",
    "open_attribute_table": "Open the attribute table[ of {layer_name}]",

    "set_layer_style": "Style {layer_name}",
    "set_raster_style": "Style {layer_name}",
    "apply_style_qml": "Apply the style {path} to {layer_name}",
    "save_style_qml": "Save the style of {layer_name}",
    "set_layer_labels": "Label {layer_name}[ by {field}]",
    "create_hillshade": "Create a hillshade[ from {layer_name}]",

    "get_features": "Read features of {layer_name}",
    "identify_features": "Identify features at a point",
    "get_selection": "Read the selection[ of {layer_name}]",
    "select_features": "Select features in {layer_name}",
    "select_by_attribute": "Select in {layer_name} where {field_name} {operator} {value}",
    "select_by_geometry": "Select in {layer_name} by shape",
    "clear_selection": "Clear the selection[ of {layer_name}]",
    "add_features": "Add features to {layer_name}",
    "update_features": "Update features of {layer_name}",
    "update_feature_geometry": "Reshape a feature of {layer_name}",
    "delete_features": "Delete features from {layer_name}",
    "add_field": "Add the field {field_name} to {layer_name}",
    "delete_field": "Delete the field {field_name} from {layer_name}",
    "rename_field": "Rename {old_name} to {new_name} in {layer_name}",
    "field_calculator": "Calculate {field_name} in {layer_name}",
    "get_field_statistics": "Statistics of {field} in {layer_name}",
    "get_unique_values": "Unique values of {field} in {layer_name}",
    "check_geometry_validity": "Check the geometries of {layer_name}",
    "add_table_join": "Join a table to {layer_name}",

    "evaluate_expression": "Evaluate an expression",
    "validate_expression": "Check an expression",
    "execute_sql": "Run a SQL query",

    "spatial_join": "Join {join_layer} onto {target_layer}",
    "zonal_statistics": "Zonal statistics of {raster_layer} in {polygon_layer}",
    "raster_calculator": "Raster calculation[ as {name}]",
    "raster_sample": "Sample {layer_name} at a point",
    "get_raster_band_stats": "Statistics of {layer_name}[, band {band}]",
    "measure_distance": "Measure a distance",
    "transform_coordinates": "Convert coordinates to {target_crs}",
    "list_algorithms": "Search the processing tools[ for {query}]",
    "find_processing_algorithm": "Search the processing tools[ for {query}]",
    "get_algorithm_help": "Read the help of {algorithm_id}",
    "get_task_status": "Check the running task",
    "cancel_task": "Cancel the running task",

    "geocode": "Find {query} on the map",
    "reverse_geocode": "Find the address at a point",
    "fetch_osm_data": "Fetch OpenStreetMap data[ as {layer_name}]",
    "fetch_building_footprints": "Fetch building footprints[ as {layer_name}]",
    "fetch_overture": "Fetch Overture {theme}[ as {layer_name}]",
    "get_route": "Compute a route",
    "add_xyz_layer": "Add the basemap[ {name}]",
    "add_wms_layer": "Add the WMS layer[ {name}]",
    "add_wfs_layer": "Add the WFS layer[ {name}]",
    "add_arcgis_rest_layer": "Add the ArcGIS layer[ {name}]",
    "add_cog_layer": "Add the raster[ {name}]",
    "add_stac_layer": "Add the satellite image[ {name}]",
    "add_pmtiles_layer": "Add the tiles[ {name}]",
    "search_stac_items": "Search satellite images",
    "list_stac_collections": "List the satellite catalogs",
    "search_open_data": "Search open data[ for {query}]",
    "inspect_data_source": "Inspect {url}",
    "find_local_data": "Look for data on this computer",
    "search_earthdata_collections": "Search NASA Earthdata[ for {query}]",
    "search_earthdata_granules": "Search NASA Earthdata files",
    "add_gee_dataset": "Add the Earth Engine dataset[ {name}]",
    "gee_compute_index": "Compute an index with Earth Engine",
    "gee_zonal_stats": "Zonal statistics with Earth Engine",

    "save_project": "Save the project[ to {path}]",
    "load_project": "Open the project {path}",
    "create_new_project": "Start a new project",
    "set_project_crs": "Set the project CRS to {crs}",
    "create_print_layout": "Create the layout {name}",
    "add_layout_map": "Add a map to the layout",
    "add_layout_legend": "Add a legend to the layout",
    "add_layout_scalebar": "Add a scale bar to the layout",
    "add_layout_north_arrow": "Add a north arrow to the layout",
    "add_layout_label": "Add a label to the layout",
    "export_layout": "Export the layout",
    "list_layouts": "List the layouts",
    "render_map": "Render the map",
    "take_screenshot": "Take a screenshot",


    "take_qgis_window_screenshot": "Look at the QGIS window",
    "take_widget_screenshot": "Look at the interface",
    "accessibility_snapshot": "Read the interface",
    "open_plugin_panel": "Open the {plugin_name} panel",
    "get_python_errors": "Read the Python errors",
    "get_network_log": "Read the network log",
    "add_bookmark": "Add the bookmark {name}",

    "execute_code": "Run Python code[: {description}]",
    "batch_commands": "Run several commands",
    "check_optional_dependencies": "Check the optional dependencies",
    "install_dependency": "Install {name}",
    "list_plugins": "List the plugins",
    "ai_edit": "AI Edit: {action}",
    "ai_segment": "AI Segmentation[: detect {object_class}][ ({action})]",
}



_PROCESSING_PARAMS = (
    ("DISTANCE", "{v}"),
    ("FIELD", "by {v}"),
    ("OVERLAY", "with {v}"),
    ("INTERSECT", "with {v}"),
    ("JOIN", "with {v}"),
    ("TOLERANCE", "tolerance {v}"),
    ("TARGET_CRS", "to {v}"),
)

_OPTIONAL_RE = re.compile(r"\[([^\[\]]*)\]")
_KEY_RE = re.compile(r"\{([a-z_]+)\}")


def _short(value, limit: int = _MAX_VALUE) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _host(value) -> str:
    """The site a URL names: ``https://data.gouv.fr/a/b.geojson`` reads ``data.gouv.fr``."""

    text = str(value or "")
    host = text.split("://", 1)[1].split("/")[0].split("?")[0]
    host = host.rsplit("@", 1)[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _word(value) -> str:
    """One argument as a word: a URL becomes its site, a path its file name, a layer dict its name, a list its count."""

    if value is None or value == "":
        return ""
    if isinstance(value, dict):
        for key in ("name", "layer_name", "id", "title"):
            if value.get(key):
                return _short(value[key])
        return ""
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return _word(value[0])
        return str(len(value)) if value else ""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:.4g}"
    text = str(value)
    if "|layername=" in text:
        return _short(text.split("|layername=", 1)[1].split("|")[0])
    if "/" in text or "\\" in text:
        if "://" in text:
            return _short(_host(text)) or _short(text.split("://", 1)[0])
        base = os.path.basename(text.rstrip("/\\"))
        if base:
            return _short(base)
    return _short(text)


def _fill(template: str, args: dict) -> str:
    """Resolve ``{key}`` from ``args``; an optional ``[..."""

    def clause(match) -> str:
        inner = match.group(1)
        for key in _KEY_RE.findall(inner):
            if not _word(args.get(key)):
                return ""
        return inner

    text = _OPTIONAL_RE.sub(clause, template)
    text = _KEY_RE.sub(lambda m: _word(args.get(m.group(1))), text)
    return " ".join(text.split()).strip(" :,")


def _processing_line(args: dict) -> str:
    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    verb = algorithm.split(":")[-1].replace("_", " ").strip()
    verb = humanise_tool_name(verb) if verb else "Run processing"
    params = args.get("parameters") if isinstance(args.get("parameters"), dict) else {}
    upper = {str(k).upper(): v for k, v in params.items()}
    parts = [verb]
    for key, shape in _PROCESSING_PARAMS:
        word = _word(upper.get(key))
        if word:
            parts.append(shape.format(v=word))
            break
    source = _word(upper.get("INPUT") or upper.get("LAYERS") or upper.get("INPUT_LAYER"))
    if source:
        parts.append(f"on {source}")
    output = _word(args.get("output_name"))
    if output:
        parts.append(f"as {output}")
    return " ".join(parts)


_LAYER_KEYS = ("layer_name", "layer_id", "layer", "input", "target_layer", "polygon_layer")


def _with_layer_names(args: dict) -> dict:
    """Copy of ``args`` where a layer id reads as the layer's name."""



    if not any(key in args for key in _LAYER_KEYS):


        return args
    try:
        from qgis.core import QgsProject
        layers = QgsProject.instance().mapLayers()
    except Exception:  # noqa: BLE001
        return args
    out = args
    for key in _LAYER_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value in layers:
            if out is args:
                out = dict(args)
            out[key] = layers[value].name()
            out.setdefault("layer_name", out[key])
    return out




_OSM_PLANET_THEMES = frozenset(
    ("landuse", "pois", "waterways", "water_areas", "power", "boundaries", "railways"))
_OSM_PLANET_TEMPLATE = "Fetch OpenStreetMap {theme}[ as {layer_name}]"


def _template_for(name: str, args: dict) -> str | None:
    if name == "fetch_overture" and str(args.get("theme") or "").strip().lower() in _OSM_PLANET_THEMES:
        return _OSM_PLANET_TEMPLATE
    return _TEMPLATES.get(name)


def describe_tool_call(name: str, args, resolved: dict | None = None) -> str:
    """The one line a tool card shows for ``name`` and ``args``."""




    args = resolved if resolved is not None else \
        (_with_layer_names(args) if isinstance(args, dict) else {})
    if name in ("run_processing", "run_model"):
        return _processing_line(args)
    template = _template_for(name, args)
    if template:
        line = _fill(template, args)
        if line:
            return line
    head = humanise_tool_name(name)
    for key in _LAYER_KEYS:
        word = _word(args.get(key))
        if word:
            return f"{head} on {word}" if key != "name" else f"{head} {word}"
    return head









_CHIP_PREP_RE = r"(?:\s+(?:of|in|on|to|at|from|for|as|with|onto|by|the)\s+|\s*)"











_CHIP_FALLBACK_KEYS = ("description", "name", "path")


def _template_keys(text: str) -> list:
    return _KEY_RE.findall(text)


def _chip_key(template: str, args: dict) -> tuple[str, bool]:
    """The argument the chip shows: a layer among the required keys, else the first required key, else the first optional key that resolves."""


    required = _template_keys(_OPTIONAL_RE.sub("", template))
    for key in _LAYER_KEYS:
        if key in required and _word(args.get(key)):
            return key, False
    for key in required:
        if _word(args.get(key)):
            return key, False
    for clause in _OPTIONAL_RE.findall(template):
        for key in _template_keys(clause):
            if _word(args.get(key)):
                return key, True
    return "", False


def _verb_without(template: str, key: str, optional: bool) -> str:
    """The template with the chip's own clause taken out."""
    if not key:
        return template
    if optional:
        return _OPTIONAL_RE.sub(lambda m: "" if key in _template_keys(m.group(1)) else m.group(0),
                                template)
    return re.sub(_CHIP_PREP_RE + r"\{" + key + r"\}", " ", template, count=1)


def _processing_parts(args: dict) -> tuple[str, str]:
    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    verb = algorithm.split(":")[-1].replace("_", " ").strip()
    verb = humanise_tool_name(verb) if verb else "Run processing"
    params = args.get("parameters") if isinstance(args.get("parameters"), dict) else {}
    upper = {str(k).upper(): v for k, v in params.items()}
    for key, shape in _PROCESSING_PARAMS:
        word = _word(upper.get(key))
        if word:
            verb = f"{verb} {shape.format(v=word)}"
            break
    chip = _word(upper.get("INPUT") or upper.get("LAYERS") or upper.get("INPUT_LAYER"))
    return verb, chip or algorithm or "run_processing"


def _chip_fallback(name: str, args: dict) -> str:
    subject, _ = call_subject(name, args)
    if subject:
        return subject
    for key in _CHIP_FALLBACK_KEYS:
        word = _word(args.get(key))
        if word:
            return word


    return ""


def describe_tool_parts(name: str, args, resolved: dict | None = None) -> tuple[str, str]:
    """``(verb, chip)`` for a tool chip row: the verb in ink, the one argument in the mono chip."""




    args = resolved if resolved is not None else \
        (_with_layer_names(args) if isinstance(args, dict) else {})
    if name in ("run_processing", "run_model"):
        return _processing_parts(args)
    template = _template_for(name, args)
    if template:
        key, optional = _chip_key(template, args)
        verb = _fill(_verb_without(template, key, optional), args)
        chip = _word(args.get(key)) if key else _chip_fallback(name, args)
        return (verb or humanise_tool_name(name)), chip
    head = humanise_tool_name(name)
    for key in _LAYER_KEYS:
        word = _word(args.get(key))
        if word:
            return head, word
    return head, _chip_fallback(name, args)




_CODE_TOOL_NAMES = ("execute_code", "run_code", "run_python", "python", "run_processing",
                    "run_model", "batch_commands", "execute_sql", "evaluate_expression",
                    "validate_expression")
_SEARCH_PREFIXES = ("search_", "find_", "geocode", "reverse_geocode", "list_algorithms",
                    "inspect_data_source", "fetch_")
_WRITE_PREFIXES = ("set_", "add_", "update_", "delete_", "create_", "rename_", "remove_",
                   "save_", "export_", "apply_", "move_", "field_calculator", "duplicate_",
                   "load_project", "install_", "ai_")
_PROCESSING_NAMES = ("run_processing", "run_model", "batch_commands")
_EXPRESSION_NAMES = ("evaluate_expression", "validate_expression", "execute_sql")



FAMILY_CONNECTOR = "connector"
FAMILY_PLUGIN = "plugin"
FAMILY_QGIS = "qgis"
COLOUR_CONNECTOR = "accent_ink"
COLOUR_PLUGIN = "orange"
COLOUR_QGIS = "ink_2"
FAMILY_GLYPHS = {FAMILY_CONNECTOR: "globe", FAMILY_PLUGIN: "puzzle", FAMILY_QGIS: "gear"}
FAMILY_COLOUR_TOKENS = {FAMILY_CONNECTOR: COLOUR_CONNECTOR, FAMILY_PLUGIN: COLOUR_PLUGIN,
                        FAMILY_QGIS: COLOUR_QGIS}
FAMILY_ORDER = (FAMILY_CONNECTOR, FAMILY_PLUGIN, FAMILY_QGIS)



_PLUGIN_TOOL_NAMES = frozenset({
    "run_plugin_flow", "install_plugin", "list_plugins", "get_plugin_info",
    "search_plugin_repository", "set_plugin_enabled", "open_plugin_panel",
    "reload_plugin", "trigger_plugin_action", "trigger_menu_action",
    "get_plugin_debug_context",
})
_PLUGIN_PREFIXES = ("plugin_", "qgis_plugin", "ai_edit_", "ai_segment_")


def base_tool_glyph(name: str) -> str:
    """The QGIS-side glyph of ``name``: a cogwheel for processing, a terminal for code, the layers for a layer tool, a pencil for a write or an."""


    name = str(name or "")
    if name in _PROCESSING_NAMES:
        return "gear"
    if name in _EXPRESSION_NAMES:
        return "code"
    if name in _CODE_TOOL_NAMES or name.endswith("_code"):
        return "terminal"
    if name.startswith(_SEARCH_PREFIXES):
        return "search"
    if "layer" in name and not name.startswith(("set_layer_style", "set_layer_labels", "set_raster_style")):
        return "layers"
    if name.startswith(_WRITE_PREFIXES):
        return "pencil"
    return "file"


def is_plugin_tool(name) -> bool:
    """A call that went through a QGIS plugin rather than QGIS itself."""
    name = str(name or "")
    return (name in _PLUGIN_TOOL_NAMES or name.startswith(_PLUGIN_PREFIXES)
            or "plugin_flow" in name)


def tool_family(name: str, args=None) -> str:
    """Which of the three worlds a call reached: ``"connector"`` for an external data source, ``"plugin"`` for a QGIS plugin, ``"qgis"`` for a."""







    if is_plugin_tool(name):
        return FAMILY_PLUGIN
    try:
        from .shared import connector_for_call

        if connector_for_call(name, args) is not None:
            return FAMILY_CONNECTOR
    except Exception:  # nosec B110 - noqa: BLE001 - no connector list means no connector family
        pass
    return FAMILY_QGIS


def tool_glyph(name: str, args=None) -> tuple[str, str, str]:
    """``(glyph, colour token, family)`` for the row of ``name``."""






    family = tool_family(name, args)
    if family == FAMILY_PLUGIN:
        return "puzzle", COLOUR_PLUGIN, family
    if family == FAMILY_CONNECTOR:
        return "globe", COLOUR_CONNECTOR, family
    return base_tool_glyph(name), COLOUR_QGIS, family






_OSM_TAG_RE = re.compile(r'\[\s*"?([A-Za-z][\w:]*)"?\s*(?:[=~!]+\s*"?([\w:.\-]+)"?)?\s*\]')
_OSM_SKIP = frozenset({"out", "bbox", "timeout", "maxsize", "date", "diff", "adiff", "csv"})
_AREA_KEYS = ("bbox", "extent", "area", "aoi", "geometry", "envelope")
_SUBJECT_KEYS = ("query", "q", "search", "address", "place", "question", "topic",
                 "keywords", "dataset", "dataset_name", "dataset_id", "collection",
                 "layer_name", "title", "name")



_RAW_VALUE_RE = re.compile(
    r"(?is)\[out:|\{\{bbox\}\}|\bout\s+(?:body|skel|meta|count)\b|://"
    r"|\bselect\b[\s\S]*\bfrom\b|\bwhere\b\s+\S+\s*[=<>]"
    r"|\b(?:nwr|way|node|rel|relation)\s*\[")


def _is_raw(text: str) -> bool:
    """Whether a value is the request rather than a word for it."""
    return bool(_RAW_VALUE_RE.search(str(text or "")))


def osm_subject(query) -> str:
    """The tag an Overpass query asks for: ``building``, ``amenity=cafe``."""
    for key, value in _OSM_TAG_RE.findall(str(query or "")):
        if key.lower() in _OSM_SKIP:
            continue
        return f"{key}={value}" if value else key
    return ""


def call_subject(name: str, args) -> tuple[str, bool]:
    """``(what the call asked for, whether it asked over an area)``."""





    args = args if isinstance(args, dict) else {}
    tags = args.get("tags") or args.get("tag")
    subject = ""
    if isinstance(tags, dict) and tags:
        key, value = next(iter(tags.items()))
        subject = f"{key}={value}" if value not in (None, "", True) else str(key)
    elif isinstance(tags, (list, tuple)) and tags:
        subject = str(tags[0])
    elif isinstance(tags, str) and tags.strip():
        subject = tags.strip()
    if not subject:
        subject = osm_subject(args.get("query") or args.get("data") or "")
    if not subject:
        for key in _SUBJECT_KEYS:
            value = args.get(key)
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value if v)
            if isinstance(value, (str, int, float)) and str(value).strip():
                if _is_raw(value):
                    continue
                subject = _short(" ".join(str(value).split()), 40)
                break
    over_area = bool(subject) and any(args.get(key) for key in _AREA_KEYS)
    return subject, over_area







_PAIR_RE = re.compile(r"([A-Za-z][\w ]*?)\s*[:=]\s*([^,;\n]+)")
_COUNT_KEYS = ("feature_count", "features", "count", "rows", "matched", "n_features",
               "items", "results", "granules", "records")
_RESULT_NAME_KEYS = ("layer_name", "layer", "output_layer", "output", "name")


def result_facts(summary) -> dict:
    """``{"count": int|None, "layer": str, "facts": {...}}`` from a summary."""
    facts = {}
    for key, value in _PAIR_RE.findall(str(summary or "")):
        facts[key.strip().lower().replace(" ", "_")] = value.strip().strip('"')
    count = None
    for key in _COUNT_KEYS:
        raw = facts.get(key)
        if raw is None:
            continue
        try:
            count = int(str(raw).replace(",", "").replace(" ", ""))
        except ValueError:
            continue
        break
    layer = ""
    for key in _RESULT_NAME_KEYS:
        if facts.get(key):
            layer = facts[key]
            break
    return {"count": count, "layer": layer, "facts": facts}






_CHECK_PREFIX_RE = re.compile(
    r"^\s*check\s*:\s*([^\n]*?)"
    r"(?:,\s*(?:message|layer_name|name|count|feature_count|status|path)\s*:|$)"
)


def check_warning(summary) -> str:
    """The post-condition warning a summary carries, or an empty string."""
    match = _CHECK_PREFIX_RE.match(str(summary or ""))
    return " ".join(match.group(1).split()) if match else ""


def group_number(value) -> str:
    """``1754`` as ``1 754``: the thin space of a French thousands group, which reads as a group in every language the panel speaks."""

    try:
        return f"{int(value):,}".replace(",", "\u202f")
    except (TypeError, ValueError):
        return str(value)



EMPTY_SUMMARY_RE = re.compile(
    r"^\s*(?:status\s*[:=]\s*)?(?:ok|okay|done|success|succeeded|true|completed)\.?\s*$",
    re.IGNORECASE)





_PYTHON_TOOL_NAMES = ("execute_code", "run_code", "run_python", "python")
_SOURCE_ARG_KEYS = ("code", "script", "source")

_SCRATCH_OUTPUTS = frozenset({"", "TEMPORARY_OUTPUT", "MEMORY:", "MEMORY"})

_MAX_SENTENCE = 200
_MAX_REASON = 90


def source_text(name: str, args) -> tuple[str, str]:
    """``(text, language)`` of the payload a call carries, when a reader may genuinely want to read it: the Python of a code call, an expression."""


    args = args if isinstance(args, dict) else {}
    name = str(name or "")
    if name in _PYTHON_TOOL_NAMES or name.endswith("_code"):
        for key in _SOURCE_ARG_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value, "python"
    expression = args.get("expression")
    if isinstance(expression, str) and expression.strip():
        return expression, "text"
    return "", ""


def _code_lines(name: str, args) -> int:
    code, language = source_text(name, args)
    return len(code.strip().splitlines()) if language == "python" else 0


def _algorithm_name(args: dict) -> str:
    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    verb = algorithm.split(":")[-1].replace("_", " ").strip()
    return humanise_tool_name(verb) if verb else ""


def _processing_sentence(args: dict) -> str:
    """``Ran the Buffer algorithm on Roads, 500, into a new layer``."""
    params = args.get("parameters") if isinstance(args.get("parameters"), dict) else {}
    upper = {str(k).upper(): v for k, v in params.items()}
    algorithm = _algorithm_name(args)
    source = _word(upper.get("INPUT") or upper.get("LAYERS") or upper.get("INPUT_LAYER"))
    if algorithm and source:
        parts = [tr("Ran the {algorithm} algorithm on {layer}").format(
            algorithm=algorithm, layer=source)]
    elif algorithm:
        parts = [tr("Ran the {algorithm} algorithm").format(algorithm=algorithm)]
    else:
        parts = [tr("Ran a processing algorithm")]
    for key, shape in _PROCESSING_PARAMS:
        word = _word(upper.get(key))
        if word:
            parts.append(shape.format(v=word))
            break
    output = _word(args.get("output_name"))
    if not output and str(upper.get("OUTPUT") or "").strip().upper() not in _SCRATCH_OUTPUTS:
        output = _word(upper.get("OUTPUT"))
    parts.append(tr("into {layer}").format(layer=output) if output else tr("into a new layer"))
    return ", ".join(parts)


def call_sentence(name: str, args, source: str = "") -> str:
    """The one plain sentence an opened row shows for what was asked."""





    name = str(name or "")
    args = _with_layer_names(args) if isinstance(args, dict) else {}
    if name in ("run_processing", "run_model"):
        return _short(_processing_sentence(args), _MAX_SENTENCE)
    lines = _code_lines(name, args)
    if lines:
        line = tr("Ran a Python script of {count} lines").format(count=lines)
        why = _short(str(args.get("description") or "").strip(), 60)
        return _short(f"{line}, {why}" if why else line, _MAX_SENTENCE)
    subject, over_area = call_subject(name, args)
    if source:
        if subject and over_area:
            line = tr("Asked {source} for {what} in the current view").format(
                source=source, what=subject)
        elif subject:
            line = tr("Asked {source} for {what}").format(source=source, what=subject)
        else:
            line = tr("Asked {source} for data").format(source=source)
        return _short(line, _MAX_SENTENCE)
    return _short(describe_tool_call(name, args), _MAX_SENTENCE)


def _plain_note(summary) -> str:
    """The summary as a note a reader can read: its first value when it is a line of ``key: value`` pairs, its own words when it is a sentence."""

    text = " ".join(str(summary or "").split())
    if not text or EMPTY_SUMMARY_RE.match(text):
        return ""
    facts = result_facts(text)["facts"]
    if facts:
        value = next(iter(facts.values()))
        return _short(group_number(value) if str(value).strip().isdigit() else value, _MAX_REASON)
    return _short(text, _MAX_REASON)


def outcome_sentence(ok, summary, duration: str = "", context: str = "") -> str:
    """What the call came back with: ``24 760 found, 1.2 s``, ``into the layer Roads buffered``, ``did not work``."""





    parts = []
    if ok is None:
        parts.append(tr("still running"))
    elif not ok:
        parts.append(tr("did not work"))
    else:
        facts = result_facts(summary)
        if facts["count"] is not None:
            parts.append(tr("{count} found").format(count=group_number(facts["count"])))
        layer = _short(facts["layer"], 40)
        if layer and layer.lower() not in str(context or "").lower():
            parts.append(tr("into the layer {name}").format(name=layer))
        if not parts:
            parts.append(_plain_note(summary))
    if duration:
        parts.append(duration)
    return ", ".join(part for part in parts if part)
