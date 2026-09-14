# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import os
import re

from qgis.core import QgsApplication, QgsProject

from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from .layer_io_tools import _same_file
from .layer_lookup import _find_layer
from .processing_decisions import _LAYER_ERROR_RE



_GPKG_TABLE_RE = re.compile(r"^(?P<path>.+\.gpkg)\|layername=(?P<table>[^|]+)$", re.IGNORECASE)
_OGR_DBNAME_RE = re.compile(r"^ogr:dbname='(?P<path>[^']+)'\s+table=\"(?P<table>[^\"]+)\"", re.IGNORECASE)


def _gpkg_table_target(value: str):

    text = (value or "").strip()
    match = _GPKG_TABLE_RE.match(text) or _OGR_DBNAME_RE.match(text)
    if not match:
        return None
    return match.group("path"), match.group("table").strip()


def _fid_blocks_geopackage(layer_ids) -> bool:










    from qgis.core import QgsProject, QgsVectorLayer

    for layer_id in layer_ids or ():
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer):
            continue
        index = layer.fields().lookupField("fid")
        if index < 0:
            continue
        try:
            keys = set(layer.dataProvider().pkAttributeIndexes() or [])
        except Exception:  # noqa: BLE001
            keys = set()
        if index not in keys:
            return True
    return False


def _any_table_without_geometry(layer_ids) -> bool:

    from qgis.core import QgsProject, QgsVectorLayer

    for layer_id in layer_ids or ():
        layer = QgsProject.instance().mapLayer(layer_id)
        if isinstance(layer, QgsVectorLayer):
            try:
                if not layer.isSpatial():
                    return True
            except Exception:  # noqa: BLE001  # nosec B112
                continue
    return False

def _temporary_suffix(definition, layer_ids=()) -> str:






    for method, choices in (("supportedOutputVectorLayerExtensions", ("gpkg", "fgb", "geojson", "sqlite")),
                            ("supportedOutputRasterLayerExtensions", ("tif", "tiff"))):
        supported_method = getattr(definition, method, None)
        if not callable(supported_method):
            continue
        try:
            supported = {str(ext).lower().lstrip(".") for ext in supported_method()}
        except Exception as exc:  # noqa: BLE001
            log_warning(f"_temporary_suffix: {method} failed: {exc}")
            continue
        for ext in choices:
            if ext in supported:
                if ext == "gpkg" and _fid_blocks_geopackage(layer_ids):
                    return _fid_safe_suffix(definition, layer_ids)
                return "." + ext
    try:
        extension = str(definition.defaultFileExtension() or "").lstrip(".")
    except Exception:  # noqa: BLE001
        return ""
    return "." + extension if extension and re.fullmatch(r"[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*", extension) else ""


def _fid_safe_suffix(definition, layer_ids=()) -> str:






    try:
        supported = {str(ext).lower().lstrip(".")
                     for ext in definition.supportedOutputVectorLayerExtensions()}
    except Exception:  # noqa: BLE001
        return ".gpkg"
    order = ("sqlite", "geojson", "fgb") if _any_table_without_geometry(layer_ids) else ("fgb", "geojson", "sqlite")
    for ext in order:
        if ext in supported:
            return "." + ext
    return ".gpkg"


def _gpkg_sink_uri(path: str, table: str) -> str | None:










    if "'" in (path or "") or '"' in (table or ""):
        return None
    return f"ogr:dbname='{path}' table=\"{table}\" (geom)"


def _destination_names(alg) -> set:

    try:
        return {d.name() for d in alg.parameterDefinitions()
                if getattr(d, "isDestination", lambda: False)()}
    except Exception:  # noqa: BLE001
        return set()


_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{1,7}$")


def _reads_as_destination(value) -> bool:

    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    return (text == "TEMPORARY_OUTPUT" or text.startswith(("memory:", "ogr:", "~")) or "/" in text
            or "\\" in text or _gpkg_table_target(text) is not None or bool(_FILE_EXTENSION_RE.search(text)))


def _output_names(alg, parameters, label: str) -> tuple:




















    if not isinstance(parameters, dict):
        return parameters, [], None
    try:
        definitions = [(d.name(), bool(getattr(d, "isDestination", lambda: False)()))
                       for d in alg.parameterDefinitions()]
    except Exception:  # noqa: BLE001
        return parameters, [], None
    outputs = [name for name, destination in definitions if destination]
    if not outputs:
        return parameters, [], None
    names = [name for name, _destination in definitions]
    listed = ", ".join(outputs)
    fixed, renamed, unknown = dict(parameters), [], []
    for key in parameters:
        if key in names:
            continue
        same = [name for name in names if name.casefold() == str(key).casefold()]
        if not same:
            unknown.append(key)
            continue
        if not any(name in outputs for name in same):
            continue
        if len(same) > 1 or same[0] in parameters:
            said = (f"'{key}' matches {' and '.join(same)} of {label} without case" if len(same) > 1
                    else f"'{key}' and '{same[0]}' both give the output {same[0]} of {label}")
            return parameters, [], {
                "_error": f"{said}; nothing was run.",
                "code": "INVALID_ARGS",
                "suggestion": f"Each output takes one exact name. Output names: {listed}."}
        fixed[same[0]] = fixed.pop(key)
        renamed.append(f"{key} read as the output {same[0]}")
    stray = [key for key in unknown if _reads_as_destination(parameters[key])]
    if not stray or all(name in fixed for name in outputs):
        return fixed, renamed, None
    inputs = [name for name in names if name not in outputs]
    taken = [key for key in stray if _names_a_kept_file(parameters[key], fixed, inputs)]
    missing = _missing_inputs(alg, fixed)
    if not taken and not missing:
        return parameters, [], {
            "_error": (f"{label} has no parameter named {', '.join(stray)}, so its output would have gone to a "
                       f"temporary file instead of the path given; nothing was run. Output names: {listed}."),
            "code": "INVALID_ARGS",
            "suggestion": f"An output's path goes under its exact name. Output names: {listed}."}
    said = f"{label} has no parameter named {', '.join(stray)}"
    if missing:
        said += (f", and its input {missing[0]} is not given" if len(missing) == 1
                 else f", and its inputs {', '.join(missing)} are not given")
    said += "; nothing was run."
    if taken:
        said += (f" The value of {', '.join(taken)} is a file that already exists or one of the call's inputs, "
                 f"so it is not read as where to write.")
    if inputs:
        said += f" Input names: {', '.join(inputs)}."
    suggestion = "Each value goes under the exact name of the input it is for"
    suggestion += f"; missing: {', '.join(missing)}." if missing else "."
    if len(taken) < len(stray):
        suggestion += f" A new file to write goes under an output name: {listed}."
    return parameters, [], {"_error": said, "code": "INVALID_ARGS", "suggestion": suggestion}


_LAYER_INPUT_TYPES = frozenset({"source", "vector", "raster", "layer", "mesh", "pointcloud", "multilayer"})


def _missing_inputs(alg, parameters, layers_only: bool = False) -> list:







    missing = []
    try:
        definitions = list(alg.parameterDefinitions())
    except Exception:  # noqa: BLE001
        return missing
    for definition in definitions:
        name = _required_input_name(definition, layers_only)
        value = (parameters or {}).get(name) if name else True
        if value is None or (isinstance(value, (str, list, tuple)) and not value):
            missing.append(name)
    return missing


def _required_input_name(definition, layers_only: bool) -> str:

    name = ""
    try:
        required = (hasattr(definition, "flags") and not getattr(definition, "isDestination", lambda: False)()
                    and not _is_optional(definition) and definition.defaultValue() is None
                    and (not layers_only or str(definition.type()) in _LAYER_INPUT_TYPES))
        name = definition.name() if required else ""
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Processing parameter not checked for a missing value: {exc}")
    return name


def _names_a_kept_file(value, parameters: dict, inputs) -> bool:

    def path_of(text):
        table = _gpkg_table_target(text)
        return os.path.expanduser((table[0] if table else text.split("|", 1)[0]).strip())

    path = path_of(str(value))
    if not path:
        return False
    if os.path.exists(path):
        return True
    for key in inputs:
        given = parameters.get(key)
        for item in (given if isinstance(given, (list, tuple)) else [given]):
            if isinstance(item, str) and item.strip() and _same_file(path_of(item), path):
                return True
    return False


def _input_file_paths(parameters: dict, destination_names: set) -> set:







    paths = set()
    for key, value in (parameters or {}).items():
        if key in destination_names:
            continue
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if not isinstance(item, str) or not item:
                continue
            candidate = _gpkg_table_target(item)
            candidate = candidate[0] if candidate else item.split("|", 1)[0]
            try:
                if candidate and os.path.isfile(candidate):
                    paths.add(candidate)
            except (OSError, ValueError):
                continue
    return paths


def _release_table_layers(path: str, table: str, skip_ids=None) -> list:


    from .processing_run import remove_layers

    removed = []
    skip_ids = set(skip_ids or ())
    project = QgsProject.instance()
    wanted = f"layername={table}".lower()
    for layer_id, layer in list(project.mapLayers().items()):
        if layer_id in skip_ids:
            continue
        parts = (layer.source() or "").split("|")
        if not parts[0] or not os.path.exists(path) or not _same_file(parts[0], path):
            continue
        if any(p.strip().lower() == wanted for p in parts[1:]):
            removed += remove_layers([layer_id])
    return removed


def modified_destination_layers(path: str, table: str | None = None, skip_ids=None) -> list[str]:







    modified: list[str] = []
    skip_ids = set(skip_ids or ())
    wanted = f"layername={table}".casefold() if table else ""
    for layer_id, layer in QgsProject.instance().mapLayers().items():
        if layer_id in skip_ids:
            continue
        parts = str(layer.source() or "").split("|")
        if not parts[0] or not _same_file(parts[0], path):
            continue
        if wanted and not any(part.strip().casefold() == wanted for part in parts[1:]):
            continue
        try:
            dirty = bool(layer.isModified())
        except Exception:  # noqa: BLE001
            dirty = False
        if dirty:
            modified.append(str(layer.name() or layer_id))
    return modified


def _has_proj_db(folder: str) -> bool:

    try:
        return bool(folder) and os.path.isfile(os.path.join(folder, "proj.db"))
    except (OSError, ValueError):
        return False


def _layout_minor(folder: str) -> int | None:

    import sqlite3

    try:
        uri = "file:" + os.path.join(folder, "proj.db").replace("\\", "/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            row = db.execute("SELECT value FROM metadata "
                             "WHERE key = 'DATABASE.LAYOUT.VERSION.MINOR'").fetchone()
        return int(row[0]) if row else None
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return None


def _ensure_proj_env() -> None:
















    try:
        from osgeo import osr
        paths = [p for p in osr.GetPROJSearchPaths() if _has_proj_db(p)]
    except Exception:  # nosec B110
        paths = []


    layouts = [(_layout_minor(p), p) for p in paths]
    known = [pair for pair in layouts if pair[0] is not None]
    own_minor, own = max(known, key=lambda pair: pair[0]) if known else (None, paths[0] if paths else "")
    for var in ("PROJ_DATA", "PROJ_LIB"):
        folder = os.environ.get(var, "")
        if not _has_proj_db(folder):
            continue
        minor = _layout_minor(folder)
        if own_minor is None or minor is None or minor >= own_minor:
            return
        log_warning(f"{var}={folder} holds a proj.db with layout minor {minor}; this QGIS's PROJ "
                    f"reads {own} (layout minor {own_minor}). GDAL steps use {own}.")
        os.environ["PROJ_LIB"] = own
        os.environ["PROJ_DATA"] = own
        return
    if own:
        os.environ["PROJ_LIB"] = own




_OPTIONAL_PROVIDERS = {
    "grass": "GRASS", "grass7": "GRASS", "saga": "SAGA", "sagang": "SAGA",
    "pdal": "PDAL", "otb": "Orfeo Toolbox", "r": "R",
}


def _provider_hint(algorithm_id: str) -> dict:









    text = str(algorithm_id or "").strip()
    registry = QgsApplication.processingRegistry()
    try:
        counts: dict = {}
        every_id: list = []
        for alg in registry.algorithms():
            every_id.append(alg.id())
            counts[alg.id().split(":", 1)[0]] = counts.get(alg.id().split(":", 1)[0], 0) + 1
        registered = {p.id() for p in registry.providers()}
    except Exception:  # noqa: BLE001
        return {}
    if ":" not in text:
        return {"suggestion": "An algorithm id is provider:name, for example native:buffer. "
                              "Providers here: " + ", ".join(sorted(counts)) + "."}
    prefix, name = text.split(":", 1)
    if prefix in registered and not counts.get(prefix):
        product = _OPTIONAL_PROVIDERS.get(prefix, prefix)
        return {"suggestion": f"The {prefix} provider is loaded but offers no algorithms in this "
                              f"QGIS, which means {product} was not installed with it. "
                              f"Algorithms here: " + ", ".join(sorted(counts)) + "."}
    if prefix not in registered:
        product = _OPTIONAL_PROVIDERS.get(prefix)
        if product:
            return {"suggestion": f"There is no {prefix} provider in this QGIS. {product} is "
                                  "optional: it has to be installed, and its provider plugin "
                                  "enabled in the Plugin Manager. Providers here: "
                                  + ", ".join(sorted(counts)) + "."}
        return {"suggestion": "No provider is called " + prefix + " here. Providers: "
                              + ", ".join(sorted(counts)) + "."}









    elsewhere = sorted({other for other in every_id
                        if other.split(":", 1)[-1].casefold() == name.casefold() and other != text})
    if elsewhere:
        return {"suggestion": f"There is no {text}, but the same algorithm name is registered as "
                              + ", ".join(elsewhere) + ".",
                "closest_algorithms": elsewhere}
    import difflib
    pool = [a for a in every_id if a.startswith(prefix + ":")]
    close = difflib.get_close_matches(text, pool, n=3, cutoff=0.6) or \
        difflib.get_close_matches(name, [p.split(":", 1)[1] for p in pool], n=3, cutoff=0.6)
    if close:
        close = [c if ":" in c else f"{prefix}:{c}" for c in close]
        return {"suggestion": "Closest ids in " + prefix + ": " + ", ".join(close) + ".",
                "closest_algorithms": close}
    return {}








_SIBLING_EXTENSIONS = (".sdat", ".sgrd", ".tif", ".tiff", ".vrt", ".asc", ".img", ".bil",
                       ".gpkg", ".shp", ".geojson", ".json", ".gml", ".kml", ".csv", ".dbf")


def written_path_for(path: str) -> str:







    if not path:
        return ""
    if os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    folder = os.path.dirname(stem)
    if not folder or not os.path.isdir(folder):
        return ""
    for candidate_ext in _SIBLING_EXTENSIONS:
        if candidate_ext == ext.lower():
            continue
        candidate = stem + candidate_ext
        if os.path.isfile(candidate):
            return candidate
    base = os.path.basename(stem).casefold()
    try:
        entries = os.listdir(folder)
    except OSError:
        return ""
    for entry in entries:
        entry_stem, entry_ext = os.path.splitext(entry)
        if entry_stem.casefold() == base and entry_ext.lower() in _SIBLING_EXTENSIONS:
            return os.path.join(folder, entry)
    return ""


def destination_report(alg, parameters: dict) -> tuple:






    missing: list = []
    written: dict = {}
    try:
        definitions = alg.parameterDefinitions()
    except Exception:  # noqa: BLE001
        return missing, written
    for definition in definitions:
        if not getattr(definition, "isDestination", lambda: False)():
            continue
        value = parameters.get(definition.name())
        if not isinstance(value, str) or value in ("", "memory:", "TEMPORARY_OUTPUT"):
            continue
        target = _gpkg_table_target(value)
        path = target[0] if target else value.split("|", 1)[0]
        if not os.path.isabs(path):
            continue


        real = path if (target and os.path.exists(path)) else ("" if target else written_path_for(path))
        if real:
            written[definition.name()] = real
        else:


            missing.append(f"{definition.name()} ({path})")
    return missing, written


def _missing_destinations(alg, parameters: dict) -> list:

    return destination_report(alg, parameters)[0]


_LAYER_DESTINATION_TYPES = frozenset({
    "sink",
    "vectorDestination",
    "rasterDestination",
    "meshDestination",
    "pointCloudDestination",
    "vectorTileDestination",
})


def output_evidence_problem(alg, parameters: dict, outputs: dict) -> str:










    if not isinstance(outputs, dict):
        return "Processing returned no output map."
    try:
        definitions = alg.parameterDefinitions()
    except Exception:  # noqa: BLE001
        return ""
    for definition in definitions:
        if not getattr(definition, "isDestination", lambda: False)():
            continue
        try:
            kind = str(definition.type())
        except Exception as exc:  # noqa: BLE001
            log_warning(f"destination check: a definition's type() failed: {exc}")
            continue
        if kind not in _LAYER_DESTINATION_TYPES:
            continue
        name = definition.name()
        summary = outputs.get(name)
        if isinstance(summary, dict) and summary.get("readable") is True:
            continue
        value = parameters.get(name)
        target = _gpkg_table_target(value) if isinstance(value, str) else None
        if target:
            where = f"{target[0]} table {target[1]}"
        elif isinstance(value, str):
            where = value
        else:
            where = str(value or name)
        detail = ""
        if isinstance(summary, dict):
            detail = str(summary.get("unreadable") or summary.get("warning") or "")
        suffix = f" ({detail})" if detail else ""
        return f"{name} is not a readable {kind} at {where}{suffix}."
    return ""


def _inputs_as_layers(alg, parameters: dict, message: str) -> dict | None:












    if not _LAYER_ERROR_RE.search(message or ""):
        return None
    try:
        destinations = {d.name() for d in alg.parameterDefinitions()
                        if getattr(d, "isDestination", lambda: False)()}
    except Exception:  # noqa: BLE001
        return None
    out = dict(parameters)
    changed = False
    for key, value in parameters.items():
        if key in destinations or not isinstance(value, str):
            continue
        layer = _find_layer(value)
        if layer is None:
            continue
        out[key] = layer
        changed = True
    return out if changed else None


def _is_optional(param) -> bool:







    flag = enum_member(type(param), "Flag", "Optional", None)
    if flag is None:
        flag = getattr(param, "FlagOptional", None)
    if flag is None:
        return False
    try:
        return bool(param.flags() & flag)
    except (TypeError, ValueError, AttributeError):
        return False
