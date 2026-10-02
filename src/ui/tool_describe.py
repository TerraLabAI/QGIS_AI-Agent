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

_OPTIONAL_RE = re.compile(r"\[([^\[\]]*)\]")
_KEY_RE = re.compile(r"\{([a-z_]+)\}")


def _short(value, limit: int = _MAX_VALUE) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _site(host: str) -> str:
    host = host.lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def _host(value) -> str:









    from qgis.PyQt.QtCore import QUrl

    from ..core.links import open_data_chip
    from .source_marks import source_host

    text = str(value or "")
    own = open_data_chip(text)
    if own:
        return own
    host = _site(source_host(text))
    if not host:
        return ""
    segments = {part.lower().replace("-", "").replace("_", "")
                for part in QUrl(text).path().split("/") if part}
    try:
        from .shared import get_connectors

        connectors = get_connectors()
    except Exception:  # nosec B110
        return ""
    for connector in connectors:
        page = _site(source_host(str(connector.get("url") or "")))
        if page and (host == page or host.endswith("." + page)) and connector.get("name"):
            return str(connector["name"])
    for connector in connectors:
        key = str(connector.get("id") or "").lower().replace("-", "").replace("_", "")
        if key and key in segments and connector.get("name"):
            return str(connector["name"])
    return ""





_QUERY_KEYS = frozenset({"query", "q", "search", "topic", "keywords", "question"})
_QUERY_NOISE_RE = re.compile(r"""(?x)
    (?:^|\s)-?[A-Za-z]+:\S+      # an operator, site:ign.fr, filetype:pdf
  | (?:^|\s)-\S+                 # an excluded term
  | \b\w+_\w+\b                  # an identifier, fetch_osm_data
  | (?:^|\s)(?:OR|AND|NOT)(?=\s|$)
  | ["'`]
""")


def _query_words(value) -> str:

    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v)
    text = _QUERY_NOISE_RE.sub(" ", str(value or ""))
    return _short(" ".join(text.split()).strip(" ,;:"))


def _value_word(key: str, args: dict) -> str:


    value = args.get(key)
    if key in _QUERY_KEYS:
        return "" if _is_raw(value) else _query_words(value)
    if key in ("algorithm_id", "algorithm") and value:
        return _short(_algorithm_name({"algorithm_id": value}))
    return _word(value)


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
            return _short(_host(text))
        base = os.path.basename(text.rstrip("/\\"))
        if base:
            return _short(base)
    return _short(text)


def _fill(template: str, args: dict) -> str:


    def clause(match) -> str:
        inner = match.group(1)
        for key in _KEY_RE.findall(inner):
            if not _value_word(key, args):
                return ""
        return inner

    text = _OPTIONAL_RE.sub(clause, template)
    if any(not _value_word(key, args) for key in _KEY_RE.findall(text)):


        return ""
    text = _KEY_RE.sub(lambda m: _value_word(m.group(1), args), text)
    return _tidy(text)


def _tidy(text: str) -> str:


    text = re.sub(r"\s+([,;:.])", r"\1", " ".join(text.split()))
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)
    return text.strip(" :,;")


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







_PROCESSING_SENTENCES = {
    "native:buffer": QT_TRANSLATE_NOOP("AIAgent", "Buffer of {DISTANCE} around {INPUT}"),
    "native:singlesidedbuffer": QT_TRANSLATE_NOOP("AIAgent", "Buffer of {DISTANCE} on one side of {INPUT}"),
    "native:simplifygeometries": QT_TRANSLATE_NOOP(
        "AIAgent", "Simplify {INPUT}[ with a tolerance of {TOLERANCE}]"),
    "native:smoothgeometry": QT_TRANSLATE_NOOP("AIAgent", "Smooth {INPUT}"),
    "native:dissolve": QT_TRANSLATE_NOOP("AIAgent", "Dissolve {INPUT}[ by {FIELD}]"),
    "native:fixgeometries": QT_TRANSLATE_NOOP("AIAgent", "Fix the geometries of {INPUT}"),
    "native:clip": QT_TRANSLATE_NOOP("AIAgent", "Clip {INPUT} to {OVERLAY}"),
    "native:intersection": QT_TRANSLATE_NOOP("AIAgent", "Intersect {INPUT} with {OVERLAY}"),
    "native:difference": QT_TRANSLATE_NOOP("AIAgent", "Remove {OVERLAY} from {INPUT}"),
    "native:union": QT_TRANSLATE_NOOP("AIAgent", "Union of {INPUT}[ and {OVERLAY}]"),
    "native:reprojectlayer": QT_TRANSLATE_NOOP("AIAgent", "Reproject {INPUT} to {TARGET_CRS}"),
    "native:centroids": QT_TRANSLATE_NOOP("AIAgent", "Centroids of {INPUT}"),
    "native:mergevectorlayers": QT_TRANSLATE_NOOP("AIAgent", "Merge {LAYERS}"),
    "native:joinattributesbylocation": QT_TRANSLATE_NOOP(
        "AIAgent", "Join {JOIN} to {INPUT} by location"),
    "native:joinattributestable": QT_TRANSLATE_NOOP("AIAgent", "Join {INPUT_2} to {INPUT}"),
    "native:extractbyexpression": QT_TRANSLATE_NOOP("AIAgent", "Extract features of {INPUT}"),
    "native:extractbyattribute": QT_TRANSLATE_NOOP("AIAgent", "Extract features of {INPUT}[ by {FIELD}]"),
    "native:extractbylocation": QT_TRANSLATE_NOOP("AIAgent", "Extract features of {INPUT} by location"),
    "native:fieldcalculator": QT_TRANSLATE_NOOP("AIAgent", "Compute {FIELD_NAME} in {INPUT}"),
    "native:zonalstatisticsfb": QT_TRANSLATE_NOOP(
        "AIAgent", "Statistics of {INPUT_RASTER} in each feature of {INPUT}"),
    "native:countpointsinpolygon": QT_TRANSLATE_NOOP("AIAgent", "Count {POINTS} in each feature of {POLYGONS}"),
    "native:multiparttosingleparts": QT_TRANSLATE_NOOP("AIAgent", "Split {INPUT} into single parts"),
    "native:polygonstolines": QT_TRANSLATE_NOOP("AIAgent", "Turn {INPUT} into lines"),
    "native:voronoipolygons": QT_TRANSLATE_NOOP("AIAgent", "Voronoi polygons of {INPUT}"),
    "native:heatmapkerneldensityestimation": QT_TRANSLATE_NOOP(
        "AIAgent", "Heatmap of {INPUT}[ with a radius of {RADIUS}]"),
    "gdal:cliprasterbymasklayer": QT_TRANSLATE_NOOP("AIAgent", "Clip {INPUT} to {MASK}"),
    "gdal:cliprasterbyextent": QT_TRANSLATE_NOOP("AIAgent", "Clip {INPUT} to an extent"),
    "gdal:warpreproject": QT_TRANSLATE_NOOP("AIAgent", "Reproject {INPUT}[ to {TARGET_CRS}]"),
    "gdal:hillshade": QT_TRANSLATE_NOOP("AIAgent", "Hillshade of {INPUT}"),
    "gdal:slope": QT_TRANSLATE_NOOP("AIAgent", "Slope of {INPUT}"),
    "gdal:contour": QT_TRANSLATE_NOOP("AIAgent", "Contours of {INPUT}[ every {INTERVAL}]"),
    "gdal:rasterize": QT_TRANSLATE_NOOP("AIAgent", "Rasterize {INPUT}"),
    "gdal:polygonize": QT_TRANSLATE_NOOP("AIAgent", "Polygonize {INPUT}"),
}
_PARAM_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")

_INPUT_KEYS = ("INPUT", "LAYERS", "INPUT_LAYER", "INPUT_RASTER", "LAYER")


def _algorithm(args: dict):

    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    if not algorithm:
        return None
    try:
        from qgis.core import QgsApplication
        return QgsApplication.processingRegistry().algorithmById(algorithm)
    except Exception:  # noqa: BLE001
        return None


def _project_layer(value):


    if not isinstance(value, str) or not value.strip():
        return None
    try:
        from qgis.core import QgsProject
        project = QgsProject.instance()
        layer = project.mapLayer(value)
        if layer is not None:
            return layer
        named = project.mapLayersByName(value)
        if named:
            return named[0]


        def same(path: str) -> str:
            return os.path.normcase(os.path.normpath(path.split("|")[0]))

        if "/" in value or "\\" in value:
            wanted = same(value)
            for layer in project.mapLayers().values():
                source = layer.source()
                if source and same(source) == wanted:
                    return layer
    except Exception:  # noqa: BLE001
        return None
    return None




_LAYER_ID_SUFFIX = re.compile(r"_(?:[0-9a-f]{8}[_-][0-9a-f]{4}[_-][0-9a-f]{4}[_-][0-9a-f]{4}[_-][0-9a-f]{12}|\d{17})$",
                              re.IGNORECASE)


def _id_base(text: str) -> str:


    if not isinstance(text, str):
        return text
    base = _LAYER_ID_SUFFIX.sub("", text)
    return base.replace("_", " ").strip() if base != text and base.strip("_ ") else text


def _layer_word(value) -> str:

    if isinstance(value, (list, tuple)) and len(value) != 1:
        return _word(value)
    if isinstance(value, (list, tuple)):
        value = value[0]
    layer = _project_layer(value)

    if layer is not None:
        return layer.name()
    text = str(value) if value is not None else ""
    if ("/" in text or "\\" in text) and "://" not in text and "|layername=" not in text:

        base = os.path.splitext(os.path.basename(text.rstrip("/\\")))[0]
        if base:
            return _short(base)
    if isinstance(value, str):
        value = _id_base(value)
    return _word(value)


def _unit_of(definition, params: dict) -> str:



    try:
        from qgis.core import QgsUnitTypes
        kind = definition.type()
        if kind not in ("distance", "duration", "area", "volume"):
            return ""
        unit = None
        if kind == "distance":
            parent = definition.parentParameterName() if hasattr(definition, "parentParameterName") else ""
            layer = _project_layer(params.get(parent)) if parent else None
            if layer is not None and layer.crs().isValid():
                unit = layer.crs().mapUnits()
        if unit is None:
            unit = definition.defaultUnit()
        if "Unknown" in str(unit):

            return ""
        return QgsUnitTypes.toAbbreviatedString(unit)
    except Exception:  # noqa: BLE001
        return ""


def _crs_word(value) -> str:
    try:
        from qgis.core import QgsCoordinateReferenceSystem
        crs = value if hasattr(value, "authid") else QgsCoordinateReferenceSystem(str(value))
        if crs.isValid():
            return _short(crs.description() or crs.authid())
    except Exception:  # noqa: BLE001
        return _word(value)
    return _word(value)


def _param_word(key: str, params: dict, found) -> str:


    value = params.get(key)
    if value is None or value == "":
        return ""
    definition = found.parameterDefinition(key) if found is not None else None
    kind = definition.type() if definition is not None else ""
    if kind in ("source", "vector", "raster", "layer", "multilayer", "mesh", "pointcloud") \
            or key in _INPUT_KEYS or (definition is None and isinstance(value, str)
                                      and _project_layer(value) is not None):
        return _layer_word(value)
    if kind == "crs" or key.endswith("CRS"):
        return _crs_word(value)
    word = _word(value)
    unit = _unit_of(definition, params) if definition is not None and word else ""
    return f"{word}\u00a0{unit}" if unit else word


def _first_measure(found, params: dict) -> str:


    if found is None:
        return ""
    for definition in found.parameterDefinitions():
        if definition.type() not in ("distance", "number", "field", "crs"):
            continue
        word = _param_word(definition.name(), params, found)
        if word:
            return f"{definition.description()} {word}"
    return ""


def processing_parts(args: dict) -> tuple[str, str]:




    found = _algorithm(args)
    name = _algorithm_name(args)
    raw = args.get("parameters") if isinstance(args.get("parameters"), dict) else {}
    params = {str(k).upper(): v for k, v in raw.items()}
    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    template = _PROCESSING_SENTENCES.get(algorithm)
    if template:
        text = tr(template)

        def clause(match) -> str:
            inner = match.group(1)
            return inner if all(_param_word(k, params, found) for k in _PARAM_RE.findall(inner)) else ""

        text = _OPTIONAL_RE.sub(clause, text)
        if all(_param_word(k, params, found) for k in _PARAM_RE.findall(text)):
            return _tidy(_PARAM_RE.sub(lambda m: _param_word(m.group(1), params, found), text)), name
    source = next((_param_word(k, params, found) for k in _INPUT_KEYS if params.get(k)), "")
    head = name or tr("Run processing")
    sentence = tr("{algorithm} on {layer}").format(algorithm=head, layer=source) if source else head
    measure = _first_measure(found, params)
    if measure:
        sentence = f"{sentence}, {measure}"
    return sentence, name


def _processing_line(args: dict) -> str:
    return processing_parts(args)[0]


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
        elif isinstance(value, str) and _id_base(value) != value:
            if out is args:
                out = dict(args)
            out[key] = _id_base(value)
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


def is_processing_call(name: str) -> bool:

    return _card(name) in _PROCESSING_CARDS


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
            return tr("{tool} on {layer}").format(tool=head, layer=word) if key != "name" \
                else f"{head} {word}"
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

















_CHIP_FALLBACK_KEYS = ("description", "name", "path")


def _template_keys(text: str) -> list:
    return _KEY_RE.findall(text)


def _chip_key(template: str, args: dict) -> tuple[str, bool]:



    required = _template_keys(_OPTIONAL_RE.sub("", template))
    for key in _LAYER_KEYS:
        if key in required and _value_word(key, args):
            return key, False
    for key in required:
        if _value_word(key, args):
            return key, False
    for clause in _OPTIONAL_RE.findall(template):
        for key in _template_keys(clause):
            if _value_word(key, args):
                return key, True
    return "", False


def _verb_and_chip(template: str, key: str, optional: bool, args: dict) -> tuple[str, str]:






    if not key:
        return _fill(template, args), ""
    if optional:
        verb = _OPTIONAL_RE.sub(lambda m: "" if key in _template_keys(m.group(1)) else m.group(0), template)
        return _fill(verb, args), _value_word(key, args)
    head, _, tail = template.partition("{" + key + "}")
    if _OPTIONAL_RE.sub(lambda m: m.group(0) if _fill(m.group(0), args) else "", tail).strip(" ,;:"):
        return _fill(template, args), ""
    if not _fill(tail, args).strip() and head.strip():
        return _fill(head, args), _value_word(key, args)
    return _fill(template, args), ""


def _processing_parts(args: dict) -> tuple[str, str]:
    sentence, name = processing_parts(args)
    return sentence, name


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
        verb, chip = _verb_and_chip(template, key, optional, args)
        if not key:
            chip = _chip_fallback(name, args)
        return (verb or _tool_head(name)), chip
    head = _tool_head(name)
    for key in _LAYER_KEYS:
        word = _word(args.get(key))
        if word:
            return head, word
    return head, _chip_fallback(name, args)


def call_title(name: str, args) -> str:



    line = describe_tool_call(name, args)
    if is_processing_call(name) and isinstance(args, dict):
        tool = processing_parts(_with_layer_names(args))[1]
        if tool:
            return f"{line} \u00b7 {tool}"
    return line


def connector_line(name: str, args, what: str = "") -> str:






    args = _with_layer_names(args) if isinstance(args, dict) else {}
    template = _template_for(name, args) or ""
    asks = any(key in _QUERY_KEYS or key in ("address", "place") for key in _template_keys(template))
    if what and not asks:
        return tr("Get {what}").format(what=what)
    return describe_tool_call(name, args, args)




FAMILY_CONNECTOR = "connector"
FAMILY_PLUGIN = "plugin"
FAMILY_QGIS = "qgis"
COLOUR_CONNECTOR = "accent_ink"
COLOUR_PLUGIN = "orange"
COLOUR_QGIS = "ink_2"


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
    if subject:


        named = args.get("layer_name")
        if isinstance(named, str) and named.strip() and not _is_raw(named):
            subject = " ".join(named.split())
        else:
            key, _sep, value = subject.partition("=")
            subject = (value or key).split(":")[-1].replace("_", " ").strip("\"' ")
    if not subject:
        for key in _SUBJECT_KEYS:
            value = args.get(key)
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value if v)
            if isinstance(value, (str, int, float)) and str(value).strip():
                if _is_raw(value):
                    continue
                words = _query_words(value) if key in _QUERY_KEYS else " ".join(str(value).split())
                if not words:
                    continue
                subject = _short(words, 40)
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



_FEATURE_KEYS = ("feature_count", "features", "n_features")
_RESULT_KEYS = ("results", "items", "matched", "granules", "hits")
_ROW_KEYS = ("rows", "records")
_LIST_NOUNS = {
    "layers": QT_TRANSLATE_NOOP("AIAgent", "{n} layers"),
    "features": QT_TRANSLATE_NOOP("AIAgent", "{n} features"),
    "fields": QT_TRANSLATE_NOOP("AIAgent", "{n} fields"),
    "files": QT_TRANSLATE_NOOP("AIAgent", "{n} files"),
    "rows": QT_TRANSLATE_NOOP("AIAgent", "{n} rows"),
    "results": QT_TRANSLATE_NOOP("AIAgent", "{n} results"),
}
_LIST_NOUN_ONE = {
    "layers": QT_TRANSLATE_NOOP("AIAgent", "1 layer"),
    "features": QT_TRANSLATE_NOOP("AIAgent", "1 feature"),
    "fields": QT_TRANSLATE_NOOP("AIAgent", "1 field"),
    "files": QT_TRANSLATE_NOOP("AIAgent", "1 file"),
    "rows": QT_TRANSLATE_NOOP("AIAgent", "1 row"),
    "results": QT_TRANSLATE_NOOP("AIAgent", "1 result"),
}


def _counted_list(detail, count: int) -> str:

    import json

    text = str(detail or "").strip()
    if not text.startswith("{"):
        return ""
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    for key, value in data.items() if isinstance(data, dict) else ():
        if isinstance(value, list) and len(value) == count:
            return str(key).lower()
    return ""


def count_text(summary, detail="") -> str:




    facts = result_facts(summary)
    if facts["count"] is None:
        return ""
    count = int(facts["count"])
    keys = facts["facts"]
    if any(key in keys for key in _FEATURE_KEYS):
        noun = "features"
    elif any(key in keys for key in _RESULT_KEYS):
        noun = "results"
    elif any(key in keys for key in _ROW_KEYS):
        noun = "rows"
    else:
        noun = _counted_list(detail, count)
    if count == 1 and noun in _LIST_NOUN_ONE:
        return tr(_LIST_NOUN_ONE[noun])
    template = _LIST_NOUNS.get(noun)
    if not template:
        return ""
    return tr(template).format(n=group_number(count))




_JOB_RE = re.compile(r'"status"\s*:\s*"(?:running|queued|pending|in_progress)"')


def is_background_job(summary, detail="") -> bool:


    text = str(detail or "")
    if '"task_id"' in text and _JOB_RE.search(text):
        return True
    started = QCoreApplication.translate("ToolExecutor", "running in the background (task {id})")
    head = started.split("{", 1)[0].strip()
    return bool(head) and str(summary or "").startswith(head)






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
    sentence, name = processing_parts(args)
    if name and name.lower() not in sentence.lower():
        sentence = f"{sentence} ({name})"
    output = _word(args.get("output_name"))
    if not output and str(upper.get("OUTPUT") or "").strip().upper() not in _SCRATCH_OUTPUTS:
        output = _layer_word(upper.get("OUTPUT"))
    tail = tr("into {layer}").format(layer=output) if output else tr("into a new layer")
    return f"{sentence}, {tail}"


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
_MAX_FIRST_SENTENCE = 160


_URL_RE = re.compile(r"\b(?:https?|s3|gs|ftp)://[^\s,;)\]]+")
_IDENTIFIER_RE = re.compile(r"`?\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b`?")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?。])\s+")


def failure_reason(summary) -> str:


    text = _FAILURE_CODE_RE.sub("", str(summary or ""))
    text = " ".join(text.split())
    if not text or EMPTY_SUMMARY_RE.match(text):
        return ""
    return _short(text, _MAX_FAILURE)


def _tool_words(name: str) -> str:

    template = _english_template(name, {})
    words = _fill(tr(template), {}) if template else ""
    words = words or _tool_head(name)
    return words[:1].lower() + words[1:] if words else name


def failure_short(summary) -> str:




    text = failure_reason(summary)
    if not text:
        return ""
    first = _SENTENCE_END_RE.split(text, 1)[0]
    from .source_marks import readable_host


    first = _URL_RE.sub(lambda m: _host(m.group(0)) or readable_host(m.group(0)) or m.group(0), first)

    def words(match) -> str:
        name = match.group(1)
        known = spec(name) is not None or name in _SERVER_TEMPLATES
        return _tool_words(name) if known else match.group(0)

    return _short(_IDENTIFIER_RE.sub(words, first), _MAX_FIRST_SENTENCE)


def outcome_sentence(ok, summary, duration: str = "", context: str = "") -> str:








    parts = []
    if ok is None:
        parts.append(tr("still running"))
    elif not ok:
        head = tr("did not work")
        if duration:
            head = f"{head}, {duration}"
        reason = failure_short(summary)
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
