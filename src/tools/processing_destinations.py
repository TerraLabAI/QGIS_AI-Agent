# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""run_processing destinations: GeoPackage tables, file paths and the inputs they write over."""
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
    """(gpkg_path, table) when ``value`` names a table inside a GeoPackage, else None."""
    text = (value or "").strip()
    match = _GPKG_TABLE_RE.match(text) or _OGR_DBNAME_RE.match(text)
    if not match:
        return None
    return match.group("path"), match.group("table").strip()


def _fid_blocks_geopackage(layer_ids) -> bool:
    """An input carries a field named fid that a GeoPackage cannot take as its feature id."""









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
        except Exception:  # noqa: BLE001 - no provider answer: assume the field is a plain one
            keys = set()
        if index not in keys:
            return True
    return False


def _any_table_without_geometry(layer_ids) -> bool:
    """An input vector layer that holds no geometry at all."""
    from qgis.core import QgsProject, QgsVectorLayer

    for layer_id in layer_ids or ():
        layer = QgsProject.instance().mapLayer(layer_id)
        if isinstance(layer, QgsVectorLayer):
            try:
                if not layer.isSpatial():
                    return True
            except Exception:  # noqa: BLE001  # nosec B112 - a layer that cannot say counts as spatial
                continue
    return False

def _fid_safe_suffix(definition, layer_ids=()) -> str:
    """A temporary format that keeps a text or repeated fid field as it is: FlatGeobuf first."""





    try:
        supported = {str(ext).lower() for ext in definition.supportedOutputVectorLayerExtensions()}
    except Exception:  # noqa: BLE001 - a destination that cannot say keeps the GeoPackage
        return ".gpkg"
    order = ("sqlite", "geojson", "fgb") if _any_table_without_geometry(layer_ids) else ("fgb", "geojson", "sqlite")
    for ext in order:
        if ext in supported:
            return "." + ext
    return ".gpkg"


def _gpkg_sink_uri(path: str, table: str) -> str | None:
    """``ogr:`` connection string naming this table, or None when it cannot be written."""









    if "'" in (path or "") or '"' in (table or ""):
        return None
    return f"ogr:dbname='{path}' table=\"{table}\" (geom)"


def _destination_names(alg) -> set:
    """Names of the parameters this algorithm writes to."""
    try:
        return {d.name() for d in alg.parameterDefinitions()
                if getattr(d, "isDestination", lambda: False)()}
    except Exception:  # noqa: BLE001 - an algorithm that cannot list its parameters protects everything
        return set()


_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{1,7}$")


def _reads_as_destination(value) -> bool:
    """Whether a parameter value names somewhere to write: a path, a GeoPackage table, a temporary output."""
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    return (text == "TEMPORARY_OUTPUT" or text.startswith(("memory:", "ogr:", "~")) or "/" in text
            or "\\" in text or _gpkg_table_target(text) is not None or bool(_FILE_EXTENSION_RE.search(text)))


def _output_names(alg, parameters, label: str) -> tuple:
    """(parameters, renamed, refusal): every output of the call under the name ``alg`` gives it."""



















    if not isinstance(parameters, dict):
        return parameters, [], None
    try:
        definitions = [(d.name(), bool(getattr(d, "isDestination", lambda: False)()))
                       for d in alg.parameterDefinitions()]
    except Exception:  # noqa: BLE001 - an algorithm that cannot list its parameters is not checked here
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
                "suggestion": f"Give each output once, under its exact name. Output names: {listed}."}
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
            "suggestion": f"Pass the path under the output's exact name. Output names: {listed}."}
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
    suggestion = "Pass each value under the exact name of the input it is for"
    suggestion += f"; missing: {', '.join(missing)}." if missing else "."
    if len(taken) < len(stray):
        suggestion += f" A new file to write goes under an output name: {listed}."
    return parameters, [], {"_error": said, "code": "INVALID_ARGS", "suggestion": suggestion}


_LAYER_INPUT_TYPES = frozenset({"source", "vector", "raster", "layer", "mesh", "pointcloud", "multilayer"})


def _missing_inputs(alg, parameters, layers_only: bool = False) -> list:
    """Names of the inputs the call leaves unset that are neither optional nor given a default."""






    missing = []
    try:
        definitions = list(alg.parameterDefinitions())
    except Exception:  # noqa: BLE001 - an algorithm that cannot list its parameters misses nothing here
        return missing
    for definition in definitions:
        name = _required_input_name(definition, layers_only)
        value = (parameters or {}).get(name) if name else True
        if value is None or (isinstance(value, (str, list, tuple)) and not value):
            missing.append(name)
    return missing


def _required_input_name(definition, layers_only: bool) -> str:
    """The name of an input that is neither optional nor given a default, or "" for any other parameter."""
    name = ""
    try:
        required = (hasattr(definition, "flags") and not getattr(definition, "isDestination", lambda: False)()
                    and not _is_optional(definition) and definition.defaultValue() is None
                    and (not layers_only or str(definition.type()) in _LAYER_INPUT_TYPES))
        name = definition.name() if required else ""
    except Exception as exc:  # noqa: BLE001 - a definition that cannot describe itself is not counted
        log_warning(f"Processing parameter not checked for a missing value: {exc}")
    return name


def _names_a_kept_file(value, parameters: dict, inputs) -> bool:
    """Whether a parameter value is a file already on disk or the value of one of the call's inputs."""
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
    """Existing files the run reads from, whatever the project has open."""






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
    """Remove the project layers reading exactly this table, and only those, so the sink can overwrite it while the other tables of the GeoPackage."""

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
            removed.append(layer.name())
            project.removeMapLayer(layer_id)
    return removed


def _has_proj_db(folder: str) -> bool:
    """Whether ``folder`` is a PROJ data folder, rather than only named one."""
    try:
        return bool(folder) and os.path.isfile(os.path.join(folder, "proj.db"))
    except (OSError, ValueError):
        return False


def _ensure_proj_env() -> None:
    """GDAL algorithms run gdalwarp and friends as child processes, which need PROJ_LIB to find proj.db; the QGIS process itself knows the path."""










    if _has_proj_db(os.environ.get("PROJ_DATA", "")) or _has_proj_db(os.environ.get("PROJ_LIB", "")):
        return
    try:
        from osgeo import osr
        paths = list(osr.GetPROJSearchPaths())
    except Exception:  # nosec B110 - environment hint only
        return
    for candidate in paths:
        if os.path.isfile(os.path.join(candidate, "proj.db")):
            os.environ["PROJ_LIB"] = candidate
            return




_OPTIONAL_PROVIDERS = {
    "grass": "GRASS", "grass7": "GRASS", "saga": "SAGA", "sagang": "SAGA",
    "pdal": "PDAL", "otb": "Orfeo Toolbox", "r": "R",
}


def _provider_hint(algorithm_id: str) -> dict:
    """Why this id resolves to nothing on this machine, when the reason is local."""








    text = str(algorithm_id or "").strip()
    registry = QgsApplication.processingRegistry()
    try:
        counts: dict = {}
        for alg in registry.algorithms():
            counts[alg.id().split(":", 1)[0]] = counts.get(alg.id().split(":", 1)[0], 0) + 1
        registered = {p.id() for p in registry.providers()}
    except Exception:  # noqa: BLE001 - a registry that will not answer gives no hint
        return {}
    if ":" not in text:
        return {"suggestion": "An algorithm id is provider:name, for example native:buffer. "
                              "Providers here: " + ", ".join(sorted(counts)) + "."}
    prefix, name = text.split(":", 1)
    if prefix in registered and not counts.get(prefix):
        product = _OPTIONAL_PROVIDERS.get(prefix, prefix)
        return {"suggestion": f"The {prefix} provider is loaded but offers no algorithms in this "
                              f"QGIS, which means {product} was not installed with it. Use an "
                              f"algorithm from: " + ", ".join(sorted(counts)) + "."}
    if prefix not in registered:
        product = _OPTIONAL_PROVIDERS.get(prefix)
        if product:
            return {"suggestion": f"There is no {prefix} provider in this QGIS. {product} is "
                                  "optional: it has to be installed, and its provider plugin "
                                  "enabled in the Plugin Manager. Providers here: "
                                  + ", ".join(sorted(counts)) + "."}
        return {"suggestion": "No provider is called " + prefix + " here. Providers: "
                              + ", ".join(sorted(counts)) + "."}

    import difflib
    pool = [a.id() for a in registry.algorithms() if a.id().startswith(prefix + ":")]
    close = difflib.get_close_matches(text, pool, n=3, cutoff=0.6) or \
        difflib.get_close_matches(name, [p.split(":", 1)[1] for p in pool], n=3, cutoff=0.6)
    if close:
        close = [c if ":" in c else f"{prefix}:{c}" for c in close]
        return {"suggestion": "Closest ids in " + prefix + ": " + ", ".join(close) + ".",
                "closest_algorithms": close}
    return {}


def _missing_destinations(alg, parameters: dict) -> list:
    """Destination parameters that name a file which does not exist after the run."""
    missing = []
    for definition in alg.parameterDefinitions():
        if not getattr(definition, "isDestination", lambda: False)():
            continue
        value = parameters.get(definition.name())
        if not isinstance(value, str) or value in ("", "memory:", "TEMPORARY_OUTPUT"):
            continue
        target = _gpkg_table_target(value)
        path = target[0] if target else value.split("|", 1)[0]
        if not os.path.isabs(path):
            continue
        if not os.path.exists(path):
            missing.append(f"{definition.name()} ({os.path.basename(path)})")
    return missing


def _inputs_as_layers(alg, parameters: dict, message: str) -> dict | None:
    """``parameters`` with every input string that names an open layer replaced by the layer."""











    if not _LAYER_ERROR_RE.search(message or ""):
        return None
    try:
        destinations = {d.name() for d in alg.parameterDefinitions()
                        if getattr(d, "isDestination", lambda: False)()}
    except Exception:  # noqa: BLE001 - an algorithm that cannot list its parameters gets no retry
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
    """Whether a Processing parameter may be left out, on QGIS 3 and on QGIS 4."""






    flag = enum_member(type(param), "Flag", "Optional", None)
    if flag is None:
        flag = getattr(param, "FlagOptional", None)
    if flag is None:
        return False
    try:
        return bool(param.flags() & flag)
    except (TypeError, ValueError, AttributeError):
        return False

