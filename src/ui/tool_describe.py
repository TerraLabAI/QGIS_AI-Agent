# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






















from __future__ import annotations

import os
import re

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from ..core.tool_registry import spec
from .card_base import humanise_tool_name



TR_CONTEXT = "AIAgent"


def tr(text: str, disambiguation: str | None = None, n: int = -1) -> str:

    return QCoreApplication.translate(TR_CONTEXT, text, disambiguation, n)


_LAYER_KEYS = ("layer_name", "layer", "name", "target_layer", "polygon_layer", "input")
_MAX_VALUE = 48





_SERVER_TEMPLATES = {
    "search_stac_items": QT_TRANSLATE_NOOP("AIAgent", "Search satellite images"),
    "list_stac_collections": QT_TRANSLATE_NOOP("AIAgent", "List the satellite catalogs"),
    "search_earthdata_collections": QT_TRANSLATE_NOOP("AIAgent", "Search NASA Earthdata[ for {query}]"),
    "search_earthdata_granules": QT_TRANSLATE_NOOP("AIAgent", "Search NASA Earthdata files"),
    "search_web": QT_TRANSLATE_NOOP("AIAgent", "Search the web[ for {query}]"),
    "find_data_on_web": QT_TRANSLATE_NOOP("AIAgent", "Look for data on the web[ for {query}]"),
    "qgis_docs": QT_TRANSLATE_NOOP("AIAgent", "Read the QGIS documentation[ on {query}]"),
    "terralab_docs": QT_TRANSLATE_NOOP("AIAgent", "Read the AI Agent documentation[ on {query}]"),
    "find_datasets": QT_TRANSLATE_NOOP("AIAgent", "Search the data catalog[ for {query}]"),
    "get_earthdata_collection_info": QT_TRANSLATE_NOOP("AIAgent", "Read a NASA Earthdata collection[ {short_name}]"),
    "search_gee_catalog": QT_TRANSLATE_NOOP("AIAgent", "Search the Earth Engine catalog[ for {query}]"),
    "search_copernicus_items": QT_TRANSLATE_NOOP("AIAgent", "Search Copernicus images[ of {collection}]"),
    "get_sentinel_image": QT_TRANSLATE_NOOP("AIAgent", "Get a Sentinel image"),
    "get_dem": QT_TRANSLATE_NOOP("AIAgent", "Get an elevation model"),
    "resolve_place": QT_TRANSLATE_NOOP("AIAgent", "Look up the place[ {query}]"),
    "find_statistic": QT_TRANSLATE_NOOP("AIAgent", "Search statistics[ on {topic}]"),
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


    text = str(value or "")
    host = text.split("://", 1)[1].split("/")[0].split("?")[0]
    host = host.rsplit("@", 1)[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _word(value) -> str:


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


    def clause(match) -> str:
        inner = match.group(1)
        for key in _KEY_RE.findall(inner):
            if not _word(args.get(key)):
                return ""
        return inner

    text = _OPTIONAL_RE.sub(clause, template)
    text = _KEY_RE.sub(lambda m: _word(args.get(m.group(1))), text)
    return " ".join(text.split()).strip(" :,")


def _algorithm_name(args: dict) -> str:



    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    if algorithm:
        try:
            from qgis.core import QgsApplication
            found = QgsApplication.processingRegistry().algorithmById(algorithm)
        except Exception:  # noqa: BLE001
            found = None
        if found is not None and found.displayName():
            return found.displayName()
    verb = algorithm.split(":")[-1].replace("_", " ").strip()
    return humanise_tool_name(verb) if verb else ""


def _processing_line(args: dict) -> str:
    verb = _algorithm_name(args) or "Run processing"
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


_LAYER_KEYS = ("layer_name", "layer_id", "layer", "input", "target_layer", "polygon_layer",
               "raster_layer", "join_layer", "lrm_layer", "dem", "area", "raster")


def _with_layer_names(args: dict) -> dict:




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


def _template_for(name: str, args: dict) -> str | None:








    template = _english_template(name, args)
    return tr(template) if template else None


def _tool_head(name: str) -> str:


    from .shared import MAX_TOOL_LABELS, served_label_map

    return served_label_map("tool_labels", MAX_TOOL_LABELS).get(name) or humanise_tool_name(name)


def _card(name: str) -> str:

    declared = spec(name)
    return declared.card if declared is not None else ""



_PROCESSING_CARDS = ("algorithm", "model")


def _english_template(name: str, args: dict) -> str | None:
    declared = spec(name)
    chosen = declared.label_for(args) if declared is not None and declared.label_for is not None else ""
    if chosen:
        return chosen
    return declared.label if declared is not None and declared.label else _SERVER_TEMPLATES.get(name)


def describe_tool_call(name: str, args, resolved: dict | None = None) -> str:





    args = resolved if resolved is not None else \
        (_with_layer_names(args) if isinstance(args, dict) else {})
    if _card(name) in _PROCESSING_CARDS:
        return _processing_line(args)
    template = _template_for(name, args)
    if template:
        line = _fill(template, args)
        if line:
            return line
    head = _tool_head(name)
    for key in _LAYER_KEYS:
        word = _word(args.get(key))
        if word:
            return f"{head} on {word}" if key != "name" else f"{head} {word}"
    return head


_PATH_CHARS = 70


def _path_text(path: str) -> str:

    text = str(path)
    return text if len(text) <= _PATH_CHARS else "\u2026" + text[-(_PATH_CHARS - 1):]


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def card_effect(name: str, args) -> str:









    if not isinstance(args, dict):
        return ""
    try:
        card = _card(name)
        if card == "algorithm":
            return _processing_effect(name, args)
        if card == "gpkg":
            return _gpkg_effect(name, args)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _processing_effect(name: str, args: dict) -> str:
    from ..core import security
    from ..tools.danger import _processing_output_params
    from ..tools.guards import _file_part, _write_targets

    outputs = _processing_output_params(str(args.get("algorithm_id") or args.get("algorithm") or ""))
    params = args.get("parameters") if isinstance(args.get("parameters"), dict) else {}
    inputs = [security.expand_path(_file_part(str(v))) for k, v in params.items()
              if (outputs is None or k not in outputs) and isinstance(v, str) and os.path.isfile(
                  security.expand_path(_file_part(v)))]
    if outputs is not None and not outputs:
        return tr("This algorithm has no output of its own: it changes its input in place.")
    parts = []
    replaces_input = False
    for target in _write_targets(name, args):
        path = security.expand_path(target)

        if outputs is not None and any(_same_file(path, source) for source in inputs):
            replaces_input = True
        if os.path.isfile(path):
            parts.append(tr("It replaces the file {path}.").format(path=_path_text(path)))
        else:
            parts.append(tr("It creates the file {path}.").format(path=_path_text(path)))
    if not parts:
        return tr("The result is a new temporary layer; no file is written.")
    if replaces_input:
        parts.append(tr("That file is also its input: the original is overwritten."))
    else:
        parts.append(tr("The input is only read, not changed."))
    return " ".join(parts)


def _gpkg_effect(name: str, args: dict) -> str:
    from ..core import security
    from ..tools.guards import _gpkg_has_table, _gpkg_table_target

    raw = str(args.get("gpkg_path") or "").strip()
    if not raw:
        where = tr("It writes the layer into the project's GeoPackage.")
    else:
        path = security.expand_path(raw)
        shown = _path_text(path)
        if not os.path.isfile(path):
            where = tr("It creates the GeoPackage {path}.").format(path=shown)
        elif _gpkg_has_table(path, _gpkg_table_target(name, args)):
            where = tr("It replaces the layer's table in the existing GeoPackage {path}.").format(path=shown)
        else:
            where = tr("It adds the layer to the existing GeoPackage {path}.").format(path=shown)
    return where + " " + tr("The project then uses the saved copy; the original file is not changed.")









_CHIP_PREP_RE = r"(?:\s+(?:of|in|on|to|at|from|for|as|with|onto|by|the)\s+|\s*)"











_CHIP_FALLBACK_KEYS = ("description", "name", "path")


def _template_keys(text: str) -> list:
    return _KEY_RE.findall(text)


def _chip_key(template: str, args: dict) -> tuple[str, bool]:



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

    if not key:
        return template
    if optional:
        return _OPTIONAL_RE.sub(lambda m: "" if key in _template_keys(m.group(1)) else m.group(0),
                                template)
    return re.sub(_CHIP_PREP_RE + r"\{" + key + r"\}", " ", template, count=1)


def _processing_parts(args: dict) -> tuple[str, str]:
    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    verb = _algorithm_name(args) or "Run processing"
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





    args = resolved if resolved is not None else \
        (_with_layer_names(args) if isinstance(args, dict) else {})
    if _card(name) in _PROCESSING_CARDS:
        return _processing_parts(args)
    template = _template_for(name, args)
    if template:
        key, optional = _chip_key(template, args)
        verb = _fill(_verb_without(template, key, optional), args)
        chip = _word(args.get(key)) if key else _chip_fallback(name, args)
        return (verb or _tool_head(name)), chip
    head = _tool_head(name)
    for key in _LAYER_KEYS:
        word = _word(args.get(key))
        if word:
            return head, word
    return head, _chip_fallback(name, args)




FAMILY_CONNECTOR = "connector"
FAMILY_PLUGIN = "plugin"
FAMILY_QGIS = "qgis"
COLOUR_CONNECTOR = "accent_ink"
COLOUR_PLUGIN = "orange"
COLOUR_QGIS = "ink_2"
FAMILY_GLYPHS = {FAMILY_CONNECTOR: "lu.globe", FAMILY_PLUGIN: "lu.puzzle", FAMILY_QGIS: "lu.cog"}
FAMILY_COLOUR_TOKENS = {FAMILY_CONNECTOR: COLOUR_CONNECTOR, FAMILY_PLUGIN: COLOUR_PLUGIN,
                        FAMILY_QGIS: COLOUR_QGIS}
FAMILY_ORDER = (FAMILY_CONNECTOR, FAMILY_PLUGIN, FAMILY_QGIS)



_PLUGIN_TOOL_NAMES = frozenset({
    "run_plugin_flow", "install_plugin", "list_plugins", "get_plugin_info", "open_plugin_manager",
    "search_plugin_repository", "set_plugin_enabled", "open_plugin_panel",
    "reload_plugin", "trigger_plugin_action", "trigger_menu_action",
    "get_plugin_debug_context",
})
_PLUGIN_PREFIXES = ("plugin_", "qgis_plugin", "ai_edit_", "ai_segment_")






_GLYPH_RULES = (
    (("ask_user",), "message-circle-question"),
    (("execute_code", "run_code", "run_python"), "square-terminal"),
    (("python",), "square-terminal"),
    (("evaluate_expression", "validate_expression"), "braces"),
    (("sql", "postgis", "connection"), "database"),
    (("field_calculator",), "calculator"),
    (("search_", "find_", "list_algorithms", "qgis_docs", "search_tools"), "search"),
    (("geocode", "pick_coordinate", "bookmark"), "map-pin"),
    (("processing", "run_model", "batch", "algorithm"), "cog"),
    (("canvas", "zoom", "flash_features", "pan_", "extent"), "locate-fixed"),
    (("render_map", "screenshot", "snapshot", "animation"), "camera"),
    (("export_", "package_project", "save_", "make_layers_permanent"), "file-down"),
    (("layout", "atlas", "report", "print"), "layout-template"),
    (("label",), "tag"),
    (("style", "symbology", "renderer", "theme", "qml", "colour", "color"), "palette"),
    (("statistic", "chart", "profile", "band_stats"), "chart-column"),
    (("measure",), "ruler"),
    (("route", "isochrone", "gtfs", "flow_lines"), "route"),
    (("crs", "transform_coordinates", "georeference"), "crosshair"),
    (("hillshade", "terrain", "watershed", "drainage", "stream", "elevation", "3d"), "mountain"),
    (("grid",), "grid-3x3"),
    (("selection", "select_", "identify_features"), "square-dashed-mouse-pointer"),
    (("delete_", "remove_", "forget"), "trash-2"),
    (("features", "attribute", "unique_values", "field", "table", "join"), "table-2"),
    (("read_document", "fetch_text", "inspect_data_source", "document"), "file-text"),
    (("fetch_", "url", "wms", "wfs", "xyz", "arcgis", "stac", "cog_layer", "pmtiles",
      "gee", "overture", "osm", "web"), "globe"),
    (("topology", "validity", "check_", "diagnose"), "triangle-alert"),
    (("layer", "project", "add_data", "group"), "layers"),
    (("add_", "create_", "update_", "edit", "set_", "rename_", "move_", "apply_",
      "duplicate_"), "pencil"),
)


def base_tool_glyph(name: str) -> str:




    name = str(name or "").lower()
    for words, glyph in _GLYPH_RULES:
        if any(word in name for word in words):
            return f"lu.{glyph}"
    return "lu.cog"


def is_plugin_tool(name) -> bool:

    name = str(name or "")
    return (name in _PLUGIN_TOOL_NAMES or name.startswith(_PLUGIN_PREFIXES)
            or "plugin_flow" in name)


def tool_family(name: str, args=None) -> str:








    if is_plugin_tool(name):
        return FAMILY_PLUGIN
    try:
        from .shared import connector_for_call

        if connector_for_call(name, args) is not None:
            return FAMILY_CONNECTOR
    except Exception:  # nosec B110
        pass
    return FAMILY_QGIS


def tool_glyph(name: str, args=None) -> tuple[str, str, str]:







    family = tool_family(name, args)
    if family == FAMILY_PLUGIN:
        name = str(name or "")
        glyph = ("lu.wand-sparkles" if name.startswith("ai_edit")
                 else "lu.scan" if name.startswith("ai_segment") else "lu.puzzle")
        return glyph, COLOUR_PLUGIN, family
    if family == FAMILY_CONNECTOR:
        return "lu.globe", COLOUR_CONNECTOR, family
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

    return bool(_RAW_VALUE_RE.search(str(text or "")))


def osm_subject(query) -> str:

    for key, value in _OSM_TAG_RE.findall(str(query or "")):

        if key.lower().split(":")[0] in _OSM_SKIP:
            continue
        return f"{key}={value}" if value else key
    return ""


def call_subject(name: str, args) -> tuple[str, bool]:






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

    match = _CHECK_PREFIX_RE.match(str(summary or ""))
    return " ".join(match.group(1).split()) if match else ""


def group_number(value) -> str:


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


def _processing_sentence(args: dict) -> str:

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






    name = str(name or "")
    args = _with_layer_names(args) if isinstance(args, dict) else {}
    card = _card(name)
    if card == "script":
        return _short(tr("Saved a Processing script"), _MAX_SENTENCE)
    if card in _PROCESSING_CARDS:
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


    text = " ".join(str(summary or "").split())
    if not text or EMPTY_SUMMARY_RE.match(text):
        return ""
    facts = result_facts(text)["facts"]
    if facts:
        value = next(iter(facts.values()))
        return _short(group_number(value) if str(value).strip().isdigit() else value, _MAX_REASON)
    return _short(text, _MAX_REASON)


_FAILURE_CODE_RE = re.compile(r"^\s*[A-Z][A-Z0-9_]{2,}\s*:\s*")
_MAX_FAILURE = 240


def failure_reason(summary) -> str:


    text = _FAILURE_CODE_RE.sub("", str(summary or ""))
    text = " ".join(text.split())
    if not text or EMPTY_SUMMARY_RE.match(text):
        return ""
    return _short(text, _MAX_FAILURE)


def outcome_sentence(ok, summary, duration: str = "", context: str = "") -> str:








    parts = []
    if ok is None:
        parts.append(tr("still running"))
    elif not ok:
        head = tr("did not work")
        if duration:
            head = f"{head}, {duration}"
        reason = failure_reason(summary)
        return f"{head}. {reason}" if reason else head
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
