# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import math
import os
import re as _re
import sqlite3
import time
from urllib.parse import unquote

from ..core import limits, security, tuning
from ..core.tool_registry import spec
from .danger import _OGR_DBNAME_RE, _processing_output_params, looks_like_disk_path, paid_algorithms


WRITE_PATH_ARGS: dict[str, tuple[str, ...]] = {
    "export_layer": ("path",),
    "export_3d_model": ("path",),
    "save_layer_to_gpkg": ("gpkg_path",),
    "create_memory_layer": ("gpkg_path",),
    "save_project": ("path",),
    "create_new_project": ("path",),
    "package_project": ("output_path",),
    "export_layout": ("output_path",),
    "write_html_report": ("path",),
    "export_atlas": ("output_path",),
    "save_style_qml": ("path", "output_path", "qml_path"),
    "create_processing_model": ("path", "output_path", "model_path"),
    "take_qgis_window_screenshot": ("save_path",),
    "take_widget_screenshot": ("save_path",),
    "ref_action": ("save_path",),
    "render_map": ("save_path", "output_path"),
    "get_3d_screenshot": ("save_path",),
    "create_hillshade": ("output_path",),
    "georeference_raster": ("output_path",),
    "create_chart": ("output_path",),
    "zonal_statistics": ("output_path",),
    "spatial_join": ("output_path",),
    "raster_calculator": ("output_path", "output"),
    "geocode_layer": ("output_path",),
}
OVERWRITE_KEYS = ("overwrite", "OVERWRITE", "allow_overwrite", "replace_existing")
_OVERWRITE_KEYS = OVERWRITE_KEYS
_LIMIT_KEYS = ("limit", "max_features", "max_results", "sample_size", "max_rows", "max_items", "page_size")
_SETTINGS_DENY_PREFIX = ("terralab", "proxy", "qgis/networkandproxy", "auth", "qgis/auth", "network", "python/",
                         "pythonplugins/", "plugins/")
_SETTINGS_DENY_WORDS = ("password", "secret", "token", "apikey", "api_key", "credential", "activation", "authcfg")
_DENIED_SCHEMES = ("file", "ftp", "gopher", "data", "javascript", "dict", "ldap", "jar", "smb", "afp", "nfs", "tftp")










_FREE_TEXT_KEYS = ("code", "query", "sql", "expression", "filter", "text", "question", "content", "html", "label",
                   "action_path", "formula", "input_query", "where",


                   "quote", "place")





_ALWAYS_CONFIRM_UNTIL_SPEC = frozenset({"execute_code"})

DATA_MUTATORS = frozenset({
    "update_features", "add_features", "field_calculator", "execute_sql", "qgis_edit_commit", "rename_field",
    "add_field", "update_feature_geometry", "set_layer_crs", "add_table_join", "qgis_merge_features",
})


def always_confirm(name: str, args: dict | None = None) -> bool:







    declared = spec(name)
    rule = (declared.always_confirm if declared is not None else False) or name in _ALWAYS_CONFIRM_UNTIL_SPEC
    if not rule:
        return False
    if not isinstance(args, dict):


        return True
    return bool(rule(args)) if callable(rule) else True


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

    shown = key if len(key) <= 80 else f"{key[:77]}..."
    return f"{path}.{shown}"


def _check_argument_tree(value) -> dict | None:








    pending = [(value, "arguments", 0)]
    seen = 0
    while pending:
        item, path, depth = pending.pop()
        seen += 1
        if seen > _MAX_ARGUMENT_NODES:
            return _refusal(
                f"The arguments contain more than {_MAX_ARGUMENT_NODES:,} values.",
                "Smaller calls would fit.",
            )
        if depth > _MAX_ARGUMENT_DEPTH:
            return _refusal(
                f"{path} is nested more than {_MAX_ARGUMENT_DEPTH} levels deep.",
                "Flatter parameters, or smaller calls, would fit.",
            )
        if isinstance(item, float) and not math.isfinite(item):
            return _refusal(
                f"{path} must be a finite number, not {item!r}.",
                "an ordinary finite number.",
            )
        if isinstance(item, str) and len(item) > _MAX_ARGUMENT_STRING:
            return _refusal(
                f"{path} is over {_MAX_ARGUMENT_STRING:,} characters.",
                "A file or URL fits better than that much text in one tool call.",
            )
        if isinstance(item, str) and "\x00" in item:
            return _refusal(
                f"{path} contains a NUL character that QGIS and filesystem APIs cannot handle.",
                "Without the NUL character it would work.",
            )
        if isinstance(item, dict):
            if seen + len(pending) + len(item) > _MAX_ARGUMENT_NODES:
                return _refusal(
                    f"The arguments contain more than {_MAX_ARGUMENT_NODES:,} values.",
                    "Smaller calls would fit.",
                )
            children = []
            for key, child in item.items():
                if not isinstance(key, str):
                    return _refusal(f"{path} contains a non-text key.", "JSON object keys are made of text.")
                if len(key) > _MAX_ARGUMENT_KEY:
                    return _refusal(
                        f"{path} contains an object key over {_MAX_ARGUMENT_KEY} characters.",
                        "Short parameter names fit.",
                    )
                children.append((child, _argument_child_path(path, key), depth + 1))
            pending.extend(children)
        elif isinstance(item, (list, tuple)):
            if seen + len(pending) + len(item) > _MAX_ARGUMENT_NODES:
                return _refusal(
                    f"The arguments contain more than {_MAX_ARGUMENT_NODES:,} values.",
                    "Smaller calls would fit.",
                )
            pending.extend((child, f"{path}[{index}]", depth + 1)
                           for index, child in enumerate(item))
    return None


def _check_extent_order(name: str, args: dict) -> dict | None:

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
                "The coordinate order or an empty rectangle may be the cause.",
            )
    return None


def labels_field_refusal(args: dict) -> dict | None:









    if not args.get("enabled", True):
        return None
    field = args.get("field")
    if isinstance(field, str) and field.strip():
        return None
    return _refusal(
        "set_layer_labels needs 'field' to switch labels on: the field name, or an expression, to draw.",
        "the field to label with, or enabled=false to turn this layer's labels off.")


def _leaves_outside(value, skip: set, depth: int = 0):







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






_BARE_SCHEME_RE = _re.compile(r"data:(?:[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+|[;,])|javascript:\S")


def _scheme_of(text: str) -> str:
    head = text[:16].lower()
    for i, ch in enumerate(head):
        if ch == ":":
            if head[i:i + 3] == "://":
                return head[:i]
            return head[:i] if _BARE_SCHEME_RE.match(text[:64].lower()) else ""
        if not (ch.isalnum() or ch in "+.-"):
            return ""
    return ""





_VSI_CLOUD = ("/vsis3", "/vsigs", "/vsiaz", "/vsiadls", "/vsioss", "/vsiswift", "/vsihdfs", "/vsiwebhdfs")

_DRIVER_PREFIX_RE = _re.compile(r'^[A-Za-z][A-Za-z0-9_]{1,15}:(?=["\']|/(?!/)|https?://|ftp://)', _re.IGNORECASE)
_EMBEDDED_URL_RE = _re.compile(r"[a-z][a-z0-9+.-]{1,15}://[^\s\"'<>|\\]+", _re.IGNORECASE)

_SCHEME_START_RE = _re.compile(r"(?<![a-z0-9+.-])[a-z][a-z0-9+.-]{1,15}://", _re.IGNORECASE)


def _embedded_urls(text: str, skip_first: bool = False) -> list[str]:







    found = []
    for start in _SCHEME_START_RE.finditer(text):
        if skip_first and start.start() == 0:
            continue
        match = _EMBEDDED_URL_RE.match(text, start.start())
        if match:
            found.append(match.group(0))
    return found


def _check_one_address(text: str) -> dict | None:

    stripped = text.strip()
    if stripped.lower().startswith(tuning.names("vsi_cloud", _VSI_CLOUD)):
        return _refusal("Cloud bucket handlers (/vsis3, /vsigs, /vsiaz, ...) are not opened by the agent: they "
                        "would spend the user's own cloud credentials.",
                        "A public https URL, or a downloaded file, works instead.")
    kind, rest = security.unwrap_vsi(stripped)
    if kind == "refused":
        return _refusal("The standard streams are not opened by the agent.", "A file or a URL works too.")
    stripped = rest if kind == "remote" else stripped
    stripped = _DRIVER_PREFIX_RE.sub("", stripped).strip().strip('"\'')
    scheme = _scheme_of(stripped)
    if not scheme:
        return None
    if scheme in tuning.names("denied_schemes", _DENIED_SCHEMES):
        return _refusal(f"{scheme}:// URLs are not fetched by the agent.",
                        "An http(s) URL, or the local file attached or named, works instead.")
    if scheme in ("http", "https"):
        error = security.validate_url(stripped)
        if error:
            return _refusal(error, "")
    return None


def _check_urls(args: dict) -> dict | None:
    for text in _string_leaves(args):
        problem = _check_one_address(text)
        if problem:
            return problem




        decoded = unquote(text)
        for variant in (text, decoded) if decoded != text else (text,):
            for embedded in _embedded_urls(variant):
                problem = _check_one_address(embedded)
                if problem:
                    return problem
    return None








_UNVOUCHED_EXEMPT = frozenset({"execute_code", "ask_user", "verify_run"})
_UNVOUCHED_SHOWN = 8


def _catalog_hosts() -> set[str]:
    hosts: set[str] = set()
    try:
        from ..core import catalog

        urls = [str(row.get("url") or "") for row in catalog.basemaps().values() if isinstance(row, dict)]
        urls += [str(u) for u in catalog.overpass_mirrors()]
    except Exception:  # noqa: BLE001
        urls = []
    from ..ui.shared import get_connectors

    urls += [str(row.get("url") or "") for row in get_connectors()]
    for url in urls:
        host = security.normal_host(url)
        if host:
            hosts.add(host)
    return hosts







_PUBLIC_SECTOR_SUFFIXES = (
    ".gov", ".mil", ".int", ".europa.eu", ".gouv.fr", ".gc.ca", ".admin.ch", ".gv.at", ".bund.de",
    ".gov.uk", ".govt.nz", ".gov.au", ".gov.ie", ".gouv.qc.ca", ".gov.br", ".gov.in", ".go.jp",
    ".go.kr", ".go.id", ".go.th", ".go.ke", ".go.tz", ".go.ug",
)
_PUBLIC_SECTOR_LABELS = ("gov", "gob", "gouv", "govt")


def _public_sector_host(host: str) -> bool:


    host = str(host or "").strip().lower().rstrip(".")
    if any(host.endswith(suffix) or host == suffix[1:] for suffix in _PUBLIC_SECTOR_SUFFIXES):
        return True
    parts = host.split(".")
    return len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _PUBLIC_SECTOR_LABELS


def _host_vouched(host: str, url: str, catalog_hosts: set[str]) -> bool:
    from ..core import net, tuning

    if (security.host_is_vouched(host) or host in catalog_hosts or security.is_paired_backend_url(url)
            or _public_sector_host(host)):
        return True
    try:
        return net.host_is_stated(host) or tuning.host_policy(host) is not None
    except Exception:  # noqa: BLE001
        return False


def unvouched_urls(name: str, args, listed=()) -> list[str]:







    if name in _UNVOUCHED_EXEMPT or not isinstance(args, dict):
        return []
    listed = {str(u) for u in (listed or ()) if isinstance(u, str)}
    found: list[str] = []
    catalog_hosts: set[str] | None = None
    for text in _string_leaves(args):
        stripped = text.strip()
        kind, rest = security.unwrap_vsi(stripped)
        stripped = rest if kind == "remote" else stripped
        stripped = _DRIVER_PREFIX_RE.sub("", stripped).strip().strip('"\'')
        if _scheme_of(stripped) not in ("http", "https"):


            continue
        if stripped in listed or stripped.split("#", 1)[0] in listed:
            continue
        decoded = unquote(stripped)



        candidates = [stripped] + [url for url in _embedded_urls(decoded, skip_first=True)
                                   if url != stripped]
        for url in candidates:
            if _scheme_of(url) not in ("http", "https"):
                continue
            host = security.normal_host(url)
            if not host:
                continue
            if catalog_hosts is None:
                catalog_hosts = _catalog_hosts()
            if not _host_vouched(host, url, catalog_hosts) and url not in found:
                found.append(url)
                if len(found) >= _UNVOUCHED_SHOWN:
                    return found
    return found


def settings_key_refusal(args: dict) -> dict | None:

    key = str(args.get("key") or "").strip().lower().lstrip("/")
    if (key.startswith(tuning.names("settings_deny_prefix", _SETTINGS_DENY_PREFIX))
            or any(word in key for word in tuning.names("settings_deny_words", _SETTINGS_DENY_WORDS))):
        return _refusal(f"The setting '{args.get('key')}' holds credentials, proxy or plugin state the agent never "
                        "reads or writes.", "That setting is visible only in QGIS itself.",
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

    targets = [_with_expected_extension(name, args[k])
               for k in WRITE_PATH_ARGS.get(name, ())
               if isinstance(args.get(k), str) and args[k].strip()
               and not _names_a_temporary(name, args[k])]
    declared = spec(name)
    if declared is not None and declared.processing is not None:
        algorithm = str(args.get("algorithm_id") or args.get("algorithm") or args.get("model") or "")
        outputs = _processing_output_params(algorithm)
        for params in declared.processing(args)[1]:
            if not isinstance(params, dict):
                continue
            for key, value in params.items():
                upper = key.upper()
                is_output = key in outputs if outputs is not None else ("OUTPUT" in upper or "DEST" in upper)
                candidate = value.get("path") if isinstance(value, dict) else value
                if is_output and looks_like_disk_path(candidate):
                    targets.append(_file_part(str(candidate)))
    return targets


def _file_part(value: str) -> str:






    match = _OGR_DBNAME_RE.match(value.strip())
    return match.group("path") if match else value.split("|", 1)[0]


def _is_project_layer(value: str) -> bool:

    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        return project.mapLayer(value) is not None or bool(project.mapLayersByName(value))
    except Exception:  # noqa: BLE001
        return False


def layer_table_name(reference, strip: bool) -> str:





    if not isinstance(reference, str) or not reference.strip():
        return ""
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        layer = project.mapLayer(reference)
        if layer is None:
            matches = project.mapLayersByName(reference)
            layer = matches[0] if matches else None
        table = str(layer.name() if layer is not None else reference)
        return table.strip() if strip else table
    except Exception:  # noqa: BLE001
        return reference.strip()


def _gpkg_table_target(name: str, args: dict) -> str:

    declared = spec(name)
    table = declared.gpkg_table(args, "") if declared is not None and declared.gpkg_table is not None else None
    return table or ""


def _gpkg_has_table(path: str, table: str) -> bool | None:

    if not table or not os.path.isfile(path):
        return False
    try:



        from ..core.snapshot_files import sqlite_read_only_uri



        connection = sqlite3.connect(sqlite_read_only_uri(path), timeout=1.0, uri=True)
        try:
            row = connection.execute(
                "SELECT 1 FROM gpkg_contents WHERE lower(table_name)=lower(?) LIMIT 1", (table,),
            ).fetchone()
            return row is not None
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return None


def _check_paths(name: str, args: dict, own_files: frozenset = frozenset()) -> dict:








    writes = _write_targets(name, args)
    declared = spec(name)
    project_file = _current_project_file()
    overwrites: list[str] = []
    creates: list[str] = []
    for target in writes:
        expanded = security.expand_path(target)
        error = security.validate_path(expanded, write=True)
        if error:
            return _refusal(error, "Allowed: the project folder, your home folder or the temp folder.",
                            "PERMISSION_DENIED")
        if not os.path.exists(expanded):
            creates.append(os.path.realpath(expanded))
            continue
        own = os.path.realpath(expanded) in own_files
        if os.path.isfile(expanded):
            table = (declared.gpkg_table(args, expanded)
                     if declared is not None and declared.gpkg_table is not None else None)
            if table is not None:
                table_exists = _gpkg_has_table(expanded, table)
                if table_exists is None:
                    return _refusal(
                        f"The existing GeoPackage cannot be inspected safely: {expanded}",
                        "Another program may hold the file, or it is not a valid GeoPackage; another path helps.",
                    )
                if not table_exists:
                    continue
                if own:
                    args["overwrite"] = True
                    continue
                if not _overwrite_requested(args):
                    return _refusal(
                        f"GeoPackage table '{table}' already exists in {expanded}. Replacing it is the user's "
                        "call (overwrite=true); another layer name avoids it.",
                        "overwrite=true replaces it once the user agrees.",
                    )
                overwrites.append(os.path.realpath(expanded))
                continue
            if declared is not None and declared.saves_open_project and os.path.realpath(expanded) == project_file:
                continue
            if own:
                args["overwrite"] = True
                continue
            if not _overwrite_requested(args):
                return _refusal(
                    f"{expanded} already exists. Replacing it is the user's call (overwrite=true); "
                    "a new file name avoids it.",
                    "overwrite=true replaces it once the user agrees, or a new name avoids it.")
            overwrites.append(os.path.realpath(expanded))






    skip = {key.lower() for key in tuple(WRITE_PATH_ARGS.get(name, ())) + _FREE_TEXT_KEYS}

    written = {os.path.normcase(security.expand_path(target)) for target in writes}
    for value in _leaves_outside(args, skip):
        if not (looks_like_disk_path(value) or value.startswith("~") or os.path.isabs(value)):
            continue


        if _is_project_layer(value):
            continue


        scheme = _scheme_of(value)
        if value.startswith(("http://", "https://")) or (scheme and scheme != "file"):
            continue

        part = security.local_part(value.split("?", 1)[0]) if scheme == "file" else _file_part(value)
        if not part:
            continue
        if os.path.normcase(security.expand_path(part)) in written:
            continue


        error = security.validate_read(part)
        if error:
            return _refusal(error, "The file needs attaching in the chat or its full path typed; "
                                   "the agent never searches folders for it.", "PERMISSION_DENIED")
    return {"overwrites": overwrites, "destructive": bool(overwrites), "creates": creates}


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
                             "create_grid_layer", "add_data", "map_drainage"})







_MATERIALISING_TOOLS = frozenset({"check_topology", "geocode_layer",
                                  "field_calculator", "select_by_geometry",
                                  "update_feature_geometry", "get_field_statistics", "duplicate_layer"})


_MATERIALISED_LAYER_KEYS = ("layer", "layer_name", "input", "INPUT", "reference_layer")


_RENDER_TOOLS = frozenset({
    "render_map", "take_qgis_window_screenshot", "take_widget_screenshot", "get_3d_screenshot",
    "export_layout", "export_atlas", "export_layout_image",
})


_RENDER_SIZE_KEYS = (("width", "MAX_RENDER_WIDTH_PX"), ("height", "MAX_RENDER_HEIGHT_PX"),
                     ("max_width", "MAX_RENDER_WIDTH_PX"))












_RENDER_CLAMPING_TOOLS = frozenset({"render_map"})


def _number(value):

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



                f"{key}={ceiling} at most fits, or the area split across several calls. A "
                "layer this size also draws slowly in QGIS, so a coarser step usually beats a second "
                "attempt at the same number.")
    return None


def _lonlat_box(value) -> tuple[float, float, float, float] | None:






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









_SERVER_SIDE_RASTER = frozenset({"add_gee_dataset"})
_RASTER_SCALE_ARG = {"gee_zonal_stats": ("scale", 30.0)}


def _check_raster_pixels(name: str, area_km2: float, args: dict) -> dict | None:
    key, default = _RASTER_SCALE_ARG[name]
    scale = _number(args.get(key))
    if scale is None or scale <= 0:
        scale = default
    pixels = area_km2 * 1e6 / (scale * scale)
    ceiling = limits.current("GEE_MAX_PIXELS")
    if pixels <= ceiling:
        return None
    fits = (area_km2 * 1e6 / ceiling) ** 0.5
    return limits.refusal(
        "The bounding box", f"{pixels:,.0f} pixels at {scale:g} m", f"{ceiling:,.0f} pixels",
        f"{key} {int(fits) + 1} or more (metres per pixel) fits this box, or a smaller box.")


def _check_bbox_area(name: str, args: dict) -> dict | None:
    if name in _OWN_AREA_CHECK or name in _SERVER_SIDE_RASTER:
        return None
    box = _lonlat_box(args.get("bbox"))
    if box is None:
        return None
    area = limits.bbox_km2(*box)
    if name in _RASTER_SCALE_ARG:
        return _check_raster_pixels(name, area, args)
    ceiling = limits.current("MAX_FETCH_KM2")
    if area <= ceiling:
        return None







    fitted = limits.shrink_bbox(box, ceiling)
    retry = ""
    if fitted is not None:
        south, west, north, east = fitted


        given = args.get("bbox")
        if isinstance(given, dict):
            keys = ("xmin", "ymin", "xmax", "ymax") if "xmin" in given else ("west", "south", "east", "north")
            written = "{" + ", ".join(f'"{k}": {v}' for k, v in zip(keys, (west, south, east, north))) + "}"
        else:
            written = f"[{west}, {south}, {east}, {north}]"
        retry = (f"bbox {written} fits "
                 f"({limits.bbox_km2(south, west, north, east):,.0f} km2, same centre), or smaller. ")
    return limits.refusal(
        "The bounding box", f"{area:,.0f} km2", f"{ceiling:,.0f} km2 for one fetch",
        retry + "The city fits where the region does not. A public service "
        "either refuses a box this size or spends minutes on it. A whole country needs an extract "
        "downloaded and added as a file instead.")


def _check_render_size(name: str, args: dict) -> dict | None:
    if name not in _RENDER_TOOLS:
        return None
    max_w, max_h = limits.current("MAX_RENDER_WIDTH_PX"), limits.current("MAX_RENDER_HEIGHT_PX")
    clamps = name in _RENDER_CLAMPING_TOOLS
    for key, side in _RENDER_SIZE_KEYS:
        if clamps and key in ("width", "height"):
            continue
        wanted = _number(args.get(key))
        ceiling = limits.current(side)
        if wanted is not None and wanted > ceiling:
            return limits.refusal(
                f"'{key}'", f"{wanted:,.0f} pixels", f"{ceiling:,} pixels",





                f"{key}={ceiling} is the largest this machine serves. "
                "Nothing downstream reads more: the panel shows a few hundred pixels and the model "
                "reads it resized. A large print needs export_layout at a higher dpi.")
    width, height = _number(args.get("width")), _number(args.get("height"))
    if clamps and width and height:



        width, height = min(width, max_w), min(height, max_h)
    max_pixels = limits.current("MAX_RENDER_PIXELS")
    if width and height and width * height > max_pixels:
        return limits.refusal(
            "The image asked for", f"{width * height:,.0f} pixels", f"{max_pixels:,} pixels",
            f"Both sides fit inside {max_w} by {max_h} together, not one at a time.")





    return None


def _feature_count(reference) -> int | None:






    if not isinstance(reference, str) or not reference.strip():
        return None
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        layer = project.mapLayer(reference)
        if layer is None:
            named = project.mapLayersByName(reference)
            layer = named[0] if named else None
        from ..core.layer_order import is_remote_vector

        if layer is None or is_remote_vector(layer):

            return None
        count = int(layer.featureCount())
    except Exception:  # noqa: BLE001
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
                "QGIS, so a layer this size freezes the window for minutes. A filter, a clip to the "
                "area of interest, or native:extractbyexpression narrows it; this then runs on the "
                "extract.")
    return None


def check_size(name: str, args: dict) -> dict | None:

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
        return ("execute_sql runs one statement; separate calls handle more, and only a query runs.")
    head = body.lstrip("( \t\r\n")
    first = _SQL_WORD.match(head)
    if first is None or first.group(0).lower() not in _SQL_QUERY_STARTS:
        shown = (first.group(0) if first else head[:12]) or "nothing"
        return (f"execute_sql runs a query, and this starts with '{shown}'. "
                "SELECT or WITH runs here; the editing tools change data and ask the user first.")
    never = tuning.names("sql_never", _SQL_NEVER)
    for word in _SQL_WORD.findall(body):
        if word.lower() in never:
            return (f"'{word}' is not available in execute_sql: it reaches files and database state "
                    "outside the layers being queried. SELECT over the layers works.")
    return None


def sql_refusal(args: dict) -> dict | None:

    problem = sql_problem(args.get("query") or args.get("sql"))
    if problem:
        return {"error": problem, "code": "INVALID_ARGS",
                "suggestion": "one SELECT (or WITH ... SELECT) over the layer names."}
    return None


def paid_algorithm_refusal(args: dict) -> dict | None:








    if str(args.get("algorithm_id") or "").strip() in paid_algorithms():
        return _refusal("This algorithm spends imagery credits and is not run through run_processing.",
                        "ai_edit_generate and ai_segment_detect_auto measure the zone and tell the "
                        "user what it costs first.", "PERMISSION_DENIED")
    return None


def check_call(name: str, args: dict, own_files: frozenset = frozenset()) -> dict:




    if not isinstance(args, dict):
        return {}
    declared = spec(name)



    for checker in (
        lambda: _check_argument_tree(args),
        lambda: _check_extent_order(name, args),
        lambda: _check_urls(args),
        lambda: declared.argument_check(args) if declared is not None and declared.argument_check else None,
        lambda: check_size(name, args),
    ):
        check = checker()
        if check:
            return check
    verdict = _check_paths(name, args, own_files)
    if verdict.get("error"):
        return verdict
    _clamp_limits(args)
    return verdict


class RunBudget:


    def __init__(self, max_steps: int | None = None, max_seconds: float | None = None):



        self.max_steps = max_steps if max_steps is not None else limits.current("RUN_MAX_STEPS")
        self.max_seconds = max_seconds if max_seconds is not None else limits.current("RUN_MAX_SECONDS")
        self.steps = 0
        self.started = time.monotonic()

    def charge(self, poll: bool = False) -> str | None:

        elapsed = time.monotonic() - self.started
        if elapsed > self.max_seconds:
            return (f"This run has used {int(elapsed // 60)} minutes, over the {int(self.max_seconds // 60)} minute "
                    "cap; no further tool call runs in it, and a new user message starts a new run.")
        if poll:
            return None
        self.steps += 1
        if self.steps > self.max_steps:
            return (f"This run has made {self.steps - 1} tool calls, the cap; no further tool call "
                    "runs in it, and a new user message starts a new run.")
        return None
