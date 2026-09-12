# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Call-level guards the executor applies before a tool runs."""











from __future__ import annotations

import math
import os
import pathlib
import re as _re
import sqlite3
import time

from ..core import limits, security
from ..core.policy import MAX_FEATURES_PER_CALL  # noqa: F401  re-exported, the tests read it here
from .danger import PAID_ALGORITHMS, _processing_output_params, looks_like_disk_path


WRITE_PATH_ARGS: dict[str, tuple[str, ...]] = {
    "export_layer": ("path",),
    "save_layer_to_gpkg": ("gpkg_path",),
    "create_memory_layer": ("gpkg_path",),
    "save_project": ("path",),
    "create_new_project": ("path",),
    "package_project": ("output_path",),
    "export_layout": ("output_path",),
    "export_atlas": ("output_path",),
    "save_style_qml": ("path", "output_path", "qml_path"),
    "create_processing_model": ("path", "output_path", "model_path"),
    "take_screenshot": ("save_path",),
    "take_qgis_window_screenshot": ("save_path",),
    "take_widget_screenshot": ("save_path",),
    "ref_action": ("save_path",),
    "render_map": ("save_path", "output_path"),
    "render_detection_reveal": ("out_dir", "output_dir"),
    "render_camera_move": ("out_dir", "output_dir"),
    "get_3d_screenshot": ("save_path",),
    "create_hillshade": ("output_path",),
    "zonal_statistics": ("output_path",),
    "spatial_join": ("output_path",),
    "raster_calculator": ("output_path", "output"),
    "geocode_layer": ("output_path",),
}
_PROCESSING_TOOLS = ("run_processing", "execute_processing_batch", "run_model")

_APPEND_TOOLS = ("save_layer_to_gpkg", "create_memory_layer")
OVERWRITE_KEYS = ("overwrite", "OVERWRITE", "allow_overwrite", "replace_existing")
_OVERWRITE_KEYS = OVERWRITE_KEYS
_LIMIT_KEYS = ("limit", "max_features", "max_results", "sample_size", "max_rows", "max_items", "page_size")
_SETTINGS_DENY_PREFIX = ("terralab", "proxy", "qgis/networkandproxy", "auth", "qgis/auth", "network", "python/",
                         "pythonplugins/", "plugins/")
_SETTINGS_DENY_WORDS = ("password", "secret", "token", "apikey", "api_key", "credential", "activation", "authcfg")
_DENIED_SCHEMES = ("file", "ftp", "gopher", "data", "javascript", "dict", "ldap", "jar", "smb", "afp", "nfs", "tftp")








_FREE_TEXT_KEYS = ("code", "query", "sql", "expression", "filter", "text", "question", "content", "html", "label",
                   "action_path", "formula", "input_query", "where")






_COSTLY_INSIDE_BATCH = frozenset({"ai_segment_detect_auto", "ai_edit_generate"})

ALWAYS_CONFIRM = frozenset({
    "execute_code", "install_dependency", "trigger_plugin_action", "trigger_menu_action", "reload_plugin",



    "run_processing",
})





DATA_MUTATORS = frozenset({
    "update_features", "add_features", "field_calculator", "execute_sql", "qgis_edit_commit", "rename_field",
    "add_field", "update_feature_geometry", "set_layer_crs", "add_table_join", "qgis_merge_features",
    "encode_cells",
})


def always_confirm(name: str, args: dict | None = None) -> bool:
    """True when this call shows a card whatever the permission level."""






    if name not in ALWAYS_CONFIRM:
        return False
    if not isinstance(args, dict):


        return True
    if name == "trigger_plugin_action":
        return bool(str(args.get("action_path") or "").strip())
    if name == "trigger_menu_action":
        if args.get("list_only"):
            return False
        return bool(str(args.get("action_path") or "").strip())
    if name == "run_processing":




        return str(args.get("algorithm_id") or "").strip().lower().startswith(("script:", "model:"))
    return True


def _refusal(message: str, suggestion: str, code: str = "INVALID_ARGS") -> dict:
    return {"error": message, "suggestion": suggestion, "code": code}


def _truthy(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "yes")


def _overwrite_requested(args: dict) -> bool:
    params = args.get("parameters") if isinstance(args.get("parameters"), dict) else {}
    return any(_truthy(args.get(k)) for k in _OVERWRITE_KEYS) or any(_truthy(params.get(k)) for k in _OVERWRITE_KEYS)


def _string_leaves(value, depth: int = 0):
    if isinstance(value, str):
        yield value
    elif depth < _MAX_ARGUMENT_DEPTH and isinstance(value, dict):
        for item in value.values():
            yield from _string_leaves(item, depth + 1)
    elif depth < _MAX_ARGUMENT_DEPTH and isinstance(value, (list, tuple)):
        for item in value:
            yield from _string_leaves(item, depth + 1)


_MAX_ARGUMENT_DEPTH = 16
_MAX_ARGUMENT_NODES = 100_000
_MAX_ARGUMENT_STRING = 1_000_000
_MAX_ARGUMENT_KEY = 256


def _argument_child_path(path: str, key: str) -> str:
    """A bounded diagnostic path; never echo a whole untrusted object key."""
    shown = key if len(key) <= 80 else f"{key[:77]}..."
    return f"{path}.{shown}"


def _check_argument_tree(value) -> dict | None:
    """Reject inputs that JSON can represent but handlers cannot safely consume."""







    pending = [(value, "arguments", 0)]
    seen = 0
    while pending:
        item, path, depth = pending.pop()
        seen += 1
        if seen > _MAX_ARGUMENT_NODES:
            return _refusal(
                f"The arguments contain more than {_MAX_ARGUMENT_NODES:,} values.",
                "Split the request into smaller calls.",
            )
        if depth > _MAX_ARGUMENT_DEPTH:
            return _refusal(
                f"{path} is nested more than {_MAX_ARGUMENT_DEPTH} levels deep.",
                "Flatten the parameters or split the request into smaller calls.",
            )
        if isinstance(item, float) and not math.isfinite(item):
            return _refusal(
                f"{path} must be a finite number, not {item!r}.",
                "Pass an ordinary finite number.",
            )
        if isinstance(item, str) and len(item) > _MAX_ARGUMENT_STRING:
            return _refusal(
                f"{path} is over {_MAX_ARGUMENT_STRING:,} characters.",
                "Pass a file or URL instead of embedding that much text in one tool call.",
            )
        if isinstance(item, str) and "\x00" in item:
            return _refusal(
                f"{path} contains a NUL character that QGIS and filesystem APIs cannot handle.",
                "Remove the NUL character and call again.",
            )
        if isinstance(item, dict):
            if seen + len(pending) + len(item) > _MAX_ARGUMENT_NODES:
                return _refusal(
                    f"The arguments contain more than {_MAX_ARGUMENT_NODES:,} values.",
                    "Split the request into smaller calls.",
                )
            children = []
            for key, child in item.items():
                if not isinstance(key, str):
                    return _refusal(f"{path} contains a non-text key.", "Use JSON object keys made of text.")
                if len(key) > _MAX_ARGUMENT_KEY:
                    return _refusal(
                        f"{path} contains an object key over {_MAX_ARGUMENT_KEY} characters.",
                        "Use short parameter names.",
                    )
                children.append((child, _argument_child_path(path, key), depth + 1))
            pending.extend(children)
        elif isinstance(item, (list, tuple)):
            if seen + len(pending) + len(item) > _MAX_ARGUMENT_NODES:
                return _refusal(
                    f"The arguments contain more than {_MAX_ARGUMENT_NODES:,} values.",
                    "Split the request into smaller calls.",
                )
            pending.extend((child, f"{path}[{index}]", depth + 1)
                           for index, child in enumerate(item))
    return None


def _check_extent_order(name: str, args: dict) -> dict | None:
    """Refuse inverted or zero-area rectangles before they reach QGIS/GDAL."""
    candidates = []
    if all(key in args for key in ("xmin", "ymin", "xmax", "ymax")):
        candidates.append(("extent", args))
    bbox = args.get("bbox")
    if isinstance(bbox, dict):
        candidates.append(("bbox", bbox))
    elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        candidates.append(("bbox", dict(zip(("xmin", "ymin", "xmax", "ymax"), bbox))))
    for label, value in candidates:
        xmin = _number(value.get("xmin", value.get("west")))
        ymin = _number(value.get("ymin", value.get("south")))
        xmax = _number(value.get("xmax", value.get("east")))
        ymax = _number(value.get("ymax", value.get("north")))
        if None in (xmin, ymin, xmax, ymax):
            continue



        if xmax < xmin or ymax < ymin:
            for low, high, a, b in (("xmin", "xmax", min(xmin, xmax), max(xmin, xmax)),
                                    ("ymin", "ymax", min(ymin, ymax), max(ymin, ymax))):
                low_key = low if low in value else {"xmin": "west", "ymin": "south"}[low]
                high_key = high if high in value else {"xmax": "east", "ymax": "north"}[high]
                value[low_key], value[high_key] = a, b
            if label == "bbox" and isinstance(args.get("bbox"), (list, tuple)):
                args["bbox"] = [value["xmin"], value["ymin"], value["xmax"], value["ymax"]]
            xmin, ymin, xmax, ymax = min(xmin, xmax), min(ymin, ymax), max(xmin, xmax), max(ymin, ymax)
        if xmax <= xmin or ymax <= ymin:
            return _refusal(
                f"The {label} has no positive area: xmin/west and ymin/south must be below xmax/east and ymax/north.",
                "Correct the coordinate order and pass a non-empty rectangle.",
            )
    return None


def _check_label_field(name: str, args: dict) -> dict | None:
    """'field' is required to switch labels on, and meaningless to switch them off."""








    if name != "set_layer_labels" or not args.get("enabled", True):
        return None
    field = args.get("field")
    if isinstance(field, str) and field.strip():
        return None
    return _refusal(
        "set_layer_labels needs 'field' to switch labels on: the field name, or an expression, to draw.",
        "Pass the field to label with, or enabled=false to turn this layer's labels off.")


def _leaves_outside(value, skip: set, depth: int = 0):
    """Every string in *value*, skipping anything under a key named in *skip*."""






    if isinstance(value, str):
        yield value
    elif depth < _MAX_ARGUMENT_DEPTH and isinstance(value, dict):
        for key, item in value.items():



            if isinstance(key, str) and key.lower() in skip:
                continue
            yield from _leaves_outside(item, skip, depth + 1)
    elif depth < _MAX_ARGUMENT_DEPTH and isinstance(value, (list, tuple)):
        for item in value:
            yield from _leaves_outside(item, skip, depth + 1)


def _scheme_of(text: str) -> str:
    head = text[:16].lower()
    for i, ch in enumerate(head):
        if ch == ":":
            return head[:i] if head[i:i + 3] == "://" or head[:i] in ("data", "javascript") else ""
        if not (ch.isalnum() or ch in "+.-"):
            return ""
    return ""





_VSI_CLOUD = ("/vsis3", "/vsigs", "/vsiaz", "/vsiadls", "/vsioss", "/vsiswift", "/vsihdfs", "/vsiwebhdfs")

_DRIVER_PREFIX_RE = _re.compile(r'^[A-Za-z][A-Za-z0-9_]{1,15}:(?=["\']|/(?!/)|https?://|ftp://)', _re.IGNORECASE)
_EMBEDDED_URL_RE = _re.compile(r"[a-z][a-z0-9+.-]{1,15}://[^\s\"'<>|\\]+", _re.IGNORECASE)


def _check_one_address(text: str) -> dict | None:
    """The refusal for one address-looking string, or None."""
    stripped = text.strip()
    if any(stripped.lower().startswith(prefix) for prefix in _VSI_CLOUD):
        return _refusal("Cloud bucket handlers (/vsis3, /vsigs, /vsiaz, ...) are not opened by the agent: they "
                        "would spend the user's own cloud credentials.",
                        "Use a public https URL, or ask the user to download the file.")
    kind, rest = security.unwrap_vsi(stripped)
    if kind == "refused":
        return _refusal("The standard streams are not opened by the agent.", "Name a file or a URL instead.")
    stripped = rest if kind == "remote" else stripped
    stripped = _DRIVER_PREFIX_RE.sub("", stripped).strip().strip('"\'')
    scheme = _scheme_of(stripped)
    if not scheme:
        return None
    if scheme in _DENIED_SCHEMES:
        return _refusal(f"{scheme}:// URLs are not fetched by the agent.",
                        "Use an http(s) URL, or ask the user to attach or name the local file.")
    if scheme in ("http", "https"):
        error = security.validate_url(stripped)
        if error:
            return _refusal(error, "Use a public URL, or ask the user to confirm the address.")
    return None


def _check_urls(args: dict) -> dict | None:
    for text in _string_leaves(args):
        problem = _check_one_address(text)
        if problem:
            return problem


        for embedded in _EMBEDDED_URL_RE.findall(text):
            problem = _check_one_address(embedded)
            if problem:
                return problem
    return None


def _check_settings_key(name: str, args: dict) -> dict | None:
    if name not in ("get_setting", "set_setting"):
        return None
    key = str(args.get("key") or "").strip().lower().lstrip("/")
    if key.startswith(_SETTINGS_DENY_PREFIX) or any(word in key for word in _SETTINGS_DENY_WORDS):
        return _refusal(f"The setting '{args.get('key')}' holds credentials, proxy or plugin state the agent never "
                        "reads or writes.", "Ask the user to check that setting in QGIS themselves.",
                        "PERMISSION_DENIED")
    return None


def _current_project_file() -> str:
    try:
        from qgis.core import QgsProject

        return os.path.realpath(QgsProject.instance().fileName() or "")
    except Exception:
        return ""








_APPENDED_EXTENSION: dict[str, tuple[tuple[str, ...], str]] = {
    "create_hillshade": ((".tif", ".tiff"), ".tif"),
}


def _with_expected_extension(name: str, path: str) -> str:
    accepted, added = _APPENDED_EXTENSION.get(name, ((), ""))
    if accepted and not path.lower().endswith(accepted):
        return path + added
    return path







_TEMPORARY_OUTPUT_TOOLS = frozenset({"raster_calculator"})
_TEMPORARY_SPELLINGS = frozenset({"temporary_output", "temp", "memory"})


def _names_a_temporary(name: str, value: str) -> bool:
    text = value.strip()
    return name in _TEMPORARY_OUTPUT_TOOLS and (
        text.lower() in _TEMPORARY_SPELLINGS or text.startswith("memory:"))


def _write_targets(name: str, args: dict) -> list[str]:
    """Paths this call will create or replace on disk."""
    targets = [_with_expected_extension(name, args[k])
               for k in WRITE_PATH_ARGS.get(name, ())
               if isinstance(args.get(k), str) and args[k].strip()
               and not _names_a_temporary(name, args[k])]
    if name in _PROCESSING_TOOLS:
        algorithm = str(args.get("algorithm_id") or args.get("algorithm") or args.get("model") or "")
        outputs = _processing_output_params(algorithm)
        param_sets = args.get("parameters_list") if name == "execute_processing_batch" else [args.get("parameters")]
        for params in param_sets or []:
            if not isinstance(params, dict):
                continue
            for key, value in params.items():
                upper = key.upper()
                is_output = key in outputs if outputs is not None else ("OUTPUT" in upper or "DEST" in upper)
                candidate = value.get("path") if isinstance(value, dict) else value
                if is_output and looks_like_disk_path(candidate):
                    targets.append(str(candidate).split("|", 1)[0])
    return targets


def _gpkg_table_target(name: str, args: dict) -> str:
    """The table an append-style GeoPackage tool will actually replace."""
    if name == "create_memory_layer":
        return str(args.get("name") or "").strip()
    if name != "save_layer_to_gpkg":
        return ""
    reference = args.get("layer")
    if not isinstance(reference, str) or not reference.strip():
        return ""
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        layer = project.mapLayer(reference)
        if layer is None:
            matches = project.mapLayersByName(reference)
            layer = matches[0] if matches else None
        return str(layer.name() if layer is not None else reference).strip()
    except Exception:  # noqa: BLE001 - outside QGIS the reference itself is the best available name
        return reference.strip()


def _gpkg_has_table(path: str, table: str) -> bool | None:
    """Whether *table* exists, or None when the existing file cannot be inspected."""
    if not table or not os.path.isfile(path):
        return False
    try:



        uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, timeout=1.0, uri=True)
        try:
            row = connection.execute(
                "SELECT 1 FROM gpkg_contents WHERE lower(table_name)=lower(?) LIMIT 1", (table,),
            ).fetchone()
            return row is not None
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return None


def _check_paths(name: str, args: dict) -> dict:
    """Refuse a forbidden path; report the existing files a write would replace."""
    writes = _write_targets(name, args)
    project_file = _current_project_file()
    overwrites: list[str] = []
    for target in writes:
        expanded = security.expand_path(target)
        error = security.validate_path(expanded, write=True)
        if error:
            return _refusal(error, "Pick a path under the project folder, your home folder or the temp folder.",
                            "PERMISSION_DENIED")
        if os.path.isfile(expanded):
            if name in _APPEND_TOOLS:
                table = _gpkg_table_target(name, args)
                table_exists = _gpkg_has_table(expanded, table)
                if table_exists is None:
                    return _refusal(
                        f"The existing GeoPackage cannot be inspected safely: {expanded}",
                        "Close programs using the file, verify it is a valid GeoPackage, or choose another path.",
                    )
                if not table_exists:
                    continue
                if not _overwrite_requested(args):
                    return _refusal(
                        f"GeoPackage table '{table}' already exists in {expanded}. Pass overwrite=true only "
                        "after the user agreed to replace that table, or choose another layer name.",
                        "Tell the user which table exists and ask whether to replace it.",
                    )
                overwrites.append(os.path.realpath(expanded))
                continue
            if name == "save_project" and os.path.realpath(expanded) == project_file:
                continue
            if not _overwrite_requested(args):
                return _refusal(
                    f"{expanded} already exists. Pass overwrite=true only after the user agreed to replace it, "
                    "or choose a new file name.",
                    "Tell the user the file exists and ask whether to replace it or write a new file.")
            overwrites.append(os.path.realpath(expanded))






    skip = {key.lower() for key in tuple(WRITE_PATH_ARGS.get(name, ())) + _FREE_TEXT_KEYS}
    for value in _leaves_outside(args, skip):
        if not (looks_like_disk_path(value) or value.startswith("~") or os.path.isabs(value)):
            continue


        if value.startswith(("http://", "https://")) or _scheme_of(value):
            continue
        error = security.validate_path(value.split("|", 1)[0])
        if error:
            return _refusal(error, "Ask the user for a data file outside credential and system folders.",
                            "PERMISSION_DENIED")
    return {"overwrites": overwrites, "destructive": bool(overwrites)}


def _clamp_limits(args: dict) -> None:
    for key in _LIMIT_KEYS:
        value = args.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        ceiling = limits.current("MAX_FEATURES_PER_CALL")
        if value > ceiling:
            args[key] = ceiling
















_CREATE_COUNT_KEYS = ("count", "num_features", "number_of_points", "point_count", "n_points",
                      "max_cells", "cells", "num_cells")














_OWN_AREA_CHECK = frozenset({"fetch_osm_data", "fetch_building_footprints", "fetch_overture",
                             "add_pmtiles_layer", "create_grid_layer"})







_MATERIALISING_TOOLS = frozenset({"render_detection_reveal", "check_topology", "geocode_layer",
                                  "encode_cells", "field_calculator", "select_by_geometry",
                                  "update_feature_geometry", "get_field_statistics", "duplicate_layer"})


_MATERIALISED_LAYER_KEYS = ("layer", "layer_name", "input", "INPUT", "reference_layer")


_RENDER_TOOLS = frozenset({
    "render_map", "render_detection_reveal", "render_camera_move", "take_screenshot",
    "take_qgis_window_screenshot", "take_widget_screenshot", "get_3d_screenshot",
    "export_layout", "export_atlas", "export_layout_image",
})


_RENDER_SIZE_KEYS = (("width", "MAX_RENDER_WIDTH_PX"), ("height", "MAX_RENDER_HEIGHT_PX"),
                     ("max_width", "MAX_RENDER_WIDTH_PX"))


def _number(value):
    """The argument as a float when it is a plain number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _check_created_features(args: dict) -> dict | None:
    ceiling = limits.current("MAX_FEATURES_CREATED")
    for key in _CREATE_COUNT_KEYS:
        wanted = _number(args.get(key))
        if wanted is not None and wanted > ceiling:
            return limits.refusal(
                f"'{key}'", f"{wanted:,.0f} features", f"{ceiling:,} in one call",
                "Ask for fewer features, or cover the area in several calls and tell the user why. "
                "A layer this size is also one QGIS draws slowly, so a coarser step is usually the "
                "better answer, not a second attempt at the same number.")
    return None


def _lonlat_box(value) -> tuple[float, float, float, float] | None:
    """(south, west, north, east) when *value* is a bbox in degrees, else None."""





    if isinstance(value, dict):
        keys = ("ymin", "xmin", "ymax", "xmax") if "xmin" in value else ("south", "west", "north", "east")
        parts = [_number(value.get(k)) for k in keys]
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        west, south, east, north = (_number(v) for v in value)
        parts = [south, west, north, east]
    else:
        return None
    if any(p is None for p in parts):
        return None
    south, west, north, east = parts
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        return None
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        return None
    return south, west, north, east


def _check_bbox_area(name: str, args: dict) -> dict | None:
    if name in _OWN_AREA_CHECK:
        return None
    box = _lonlat_box(args.get("bbox"))
    if box is None:
        return None
    area = limits.bbox_km2(*box)
    ceiling = limits.current("MAX_FETCH_KM2")
    if area <= ceiling:
        return None
    return limits.refusal(
        "The bounding box", f"{area:,.0f} km2", f"{ceiling:,.0f} km2 for one fetch",
        f"Cut the box to {ceiling:,.0f} km2 around the centre of the area, or the city rather than "
        "the region, and tell the user which part you kept. Do not ask the user. A public service "
        "either refuses a box this size or spends minutes on it. For a whole country, download an "
        "extract and add the file instead.")


def _check_render_size(name: str, args: dict) -> dict | None:
    if name not in _RENDER_TOOLS:
        return None
    max_w, max_h = limits.current("MAX_RENDER_WIDTH_PX"), limits.current("MAX_RENDER_HEIGHT_PX")
    for key, side in _RENDER_SIZE_KEYS:
        wanted = _number(args.get(key))
        ceiling = limits.current(side)
        if wanted is not None and wanted > ceiling:
            return limits.refusal(
                f"'{key}'", f"{wanted:,.0f} pixels", f"{ceiling:,} pixels",
                f"Render at {max_w} by {max_h} at most. "
                "Nothing downstream reads more: the panel shows the image at a few hundred pixels "
                "and the model reads it resized. For a large print, export the layout at a higher "
                "dpi rather than asking for a larger image.")
    width, height = _number(args.get("width")), _number(args.get("height"))
    max_pixels = limits.current("MAX_RENDER_PIXELS")
    if width and height and width * height > max_pixels:
        return limits.refusal(
            "The image asked for", f"{width * height:,.0f} pixels", f"{max_pixels:,} pixels",
            "Keep the two sides inside 4K together, not only one at a time.")
    dpi = _number(args.get("dpi"))
    max_dpi = limits.current("MAX_RENDER_DPI")
    if dpi is not None and dpi > max_dpi:
        return limits.refusal(
            "'dpi'", f"{dpi:,.0f}", f"{max_dpi}",
            "600 dpi is print quality. dpi multiplies into pixels twice over, so an A0 page at "
            "2400 dpi is a three gigapixel image that fails after minutes of work.")
    return None


def _feature_count(reference) -> int | None:
    """Features in the named project layer, or None when it cannot be counted."""





    if not isinstance(reference, str) or not reference.strip():
        return None
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        layer = project.mapLayer(reference)
        if layer is None:
            named = project.mapLayersByName(reference)
            layer = named[0] if named else None
        if layer is None:
            return None
        count = int(layer.featureCount())
    except Exception:  # noqa: BLE001 - no QGIS, no such layer, or a provider that cannot count
        return None
    return count if count >= 0 else None


def _check_materialised_features(name: str, args: dict) -> dict | None:
    if name not in _MATERIALISING_TOOLS:
        return None
    ceiling = limits.current("MAX_FEATURES_MATERIALISED")
    for key in _MATERIALISED_LAYER_KEYS:
        count = _feature_count(args.get(key))
        if count is not None and count > ceiling:
            return limits.refusal(
                f"Layer '{args.get(key)}'", f"{count:,} features",
                f"{ceiling:,} for {name}",
                f"{name} reads every feature into memory before it starts, on the thread that draws "
                "QGIS, so a layer this size freezes the window for minutes. Extract the part that "
                "matters first (a filter, a clip to the area of interest, native:extractbyexpression) "
                "and run this on the extract.")
    return None


def check_size(name: str, args: dict) -> dict | None:
    """The ceilings, in one call. None when the request is inside all of them."""
    for check in (_check_created_features(args), _check_bbox_area(name, args),
                  _check_render_size(name, args), _check_materialised_features(name, args)):
        if check:
            return check
    return None






























_SQL_BLOCK_COMMENT = _re.compile(r"/\*.*?\*/", _re.DOTALL)
_SQL_LINE_COMMENT = _re.compile(r"--[^\n]*")
_SQL_STRING = _re.compile(r"'(?:[^']|'')*'")
_SQL_QUOTED_IDENT = _re.compile(r'"(?:[^"]|"")*"|\[[^\]]*\]|`[^`]*`')
_SQL_WORD = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_SQL_QUERY_STARTS = frozenset({"select", "with", "values", "table"})




_SQL_NEVER = frozenset({
    "attach", "detach", "pragma", "vacuum", "reindex", "drop", "alter", "truncate",
    "load_extension", "writefile", "readfile", "fts3_tokenizer", "randomblob",

    "blobfromfile", "blobtofile", "importshp", "exportshp", "importdbf", "exportdbf", "importxls",
    "importgeojson", "exportgeojson", "importkml", "exportkml", "importdxf", "exportdxf", "importwfs",
    "importzipshp", "importzipdbf", "importxml", "exportxml", "updatelayerstatistics", "createspatialindex",
    "disablespatialindex", "checkspatialindex", "recoverspatialindex", "invalidatelayerstatistics",
    "initspatialmetadata", "initspatialmetadatafull", "createstylingtables", "elementarygeometries",
    "removeduplicaterows", "checkgeopackagemetadata", "autogpkgstart", "autogpkgstop", "eval",
})


def sql_problem(query: object) -> str | None:
    """None when this is one plain query, else the sentence saying why not."""
    text = str(query or "")
    body = _SQL_BLOCK_COMMENT.sub(" ", text)
    body = _SQL_LINE_COMMENT.sub(" ", body)
    body = _SQL_STRING.sub("''", body)
    body = _SQL_QUOTED_IDENT.sub('""', body)
    body = body.strip()
    while body.endswith(";"):
        body = body[:-1].strip()
    if not body:
        return "The query is empty."
    if ";" in body:
        return ("execute_sql runs one statement. Split this into separate calls, "
                "and remember that only a query can be run.")
    head = body.lstrip("( \t\r\n")
    first = _SQL_WORD.match(head)
    if first is None or first.group(0).lower() not in _SQL_QUERY_STARTS:
        shown = (first.group(0) if first else head[:12]) or "nothing"
        return (f"execute_sql runs a query, and this starts with '{shown}'. "
                "Use SELECT or WITH; to change data use the editing tools, which ask the user first.")
    for word in _SQL_WORD.findall(body):
        if word.lower() in _SQL_NEVER:
            return (f"'{word}' is not available in execute_sql: it reaches files and database state "
                    "outside the layers being queried. Use SELECT over the layers instead.")
    return None


def _check_sql(name: str, args: dict) -> dict | None:
    if name != "execute_sql":
        return None
    problem = sql_problem(args.get("query") or args.get("sql"))
    if problem:
        return {"error": problem, "code": "INVALID_ARGS",
                "suggestion": "Write one SELECT (or WITH ... SELECT) over the layer names."}
    return None


def check_call(name: str, args: dict) -> dict:
    """The verdict for one call: a refusal, or {destructive, overwrites}. Never raises."""
    if not isinstance(args, dict):
        return {}


    for checker in (
        lambda: _check_argument_tree(args),
        lambda: _check_extent_order(name, args),
        lambda: _check_label_field(name, args),
        lambda: _check_settings_key(name, args),
        lambda: _check_urls(args),
        lambda: _check_sql(name, args),
        lambda: check_size(name, args),
    ):
        check = checker()
        if check:
            return check
    verdict = _check_paths(name, args)
    if verdict.get("error"):
        return verdict
    if name == "run_processing" and str(args.get("algorithm_id") or "").strip() in PAID_ALGORITHMS:





        return _refusal("This algorithm spends imagery credits and is not run through run_processing.",
                        "Call ai_edit_generate or ai_segment_detect_auto, which measure the zone and "
                        "tell the user what it costs.", "PERMISSION_DENIED")
    if name == "batch_commands":
        for command in args.get("commands") or []:
            if isinstance(command, dict):
                inner_name = str(command.get("name") or "")
                if inner_name in _COSTLY_INSIDE_BATCH:






                    return _refusal(f"{inner_name} cannot run inside batch_commands: it spends imagery "
                                    "credits and needs its own area check.",
                                    f"Call {inner_name} on its own.", "PERMISSION_DENIED")
                if always_confirm(inner_name, command.get("arguments")) or inner_name == "batch_commands":



                    return _refusal(f"{inner_name} cannot run inside batch_commands: it needs its own "
                                    "confirmation card.", f"Call {inner_name} on its own.", "PERMISSION_DENIED")
                inner = check_call(inner_name, command.get("arguments") or {})
                if inner.get("error"):
                    return inner
                verdict["overwrites"] = verdict.get("overwrites", []) + inner.get("overwrites", [])
                verdict["destructive"] = verdict.get("destructive") or inner.get("destructive", False)
    _clamp_limits(args)
    return verdict


class RunBudget:
    """Steps and wall clock of one run against the policy caps; polls are free."""

    def __init__(self, max_steps: int | None = None, max_seconds: float | None = None):



        self.max_steps = max_steps if max_steps is not None else limits.current("RUN_MAX_STEPS")
        self.max_seconds = max_seconds if max_seconds is not None else limits.current("RUN_MAX_SECONDS")
        self.steps = 0
        self.started = time.monotonic()

    def charge(self, poll: bool = False) -> str | None:
        """None when the call may run, else why the run must stop."""
        elapsed = time.monotonic() - self.started
        if elapsed > self.max_seconds:
            return (f"This run has used {int(elapsed // 60)} minutes, over the {int(self.max_seconds // 60)} minute "
                    "cap. Stop here, summarise what was done, and ask the user to continue in a new message.")
        if poll:
            return None
        self.steps += 1
        if self.steps > self.max_steps:
            return (f"This run has made {self.steps - 1} tool calls, the cap. Stop here, summarise what was done "
                    "and what is left, and ask the user to continue in a new message.")
        return None
