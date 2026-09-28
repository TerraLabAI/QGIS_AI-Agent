# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

import os
import re
from typing import Any

from ..core import security
from ..core.tool_registry import spec



UNKNOWN_DANGER = "write"



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
    "georeference_raster": ("output_path",),
    "create_chart": ("output_path",),
    "make_animation": ("output_path",),
    "export_document_report": ("output_path",),
    "create_flow_lines": ("output_path",),
}


_APPENDS_EXTENSION = {"create_hillshade": ".tif", "georeference_raster": ".tif", "create_chart": ".png",
                      "make_animation": ".gif", "create_flow_lines": ".gpkg"}





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


def paid_algorithms() -> frozenset:

    from ..core import tuning

    return tuning.check_algs("paid_algorithms", PAID_ALGORITHMS)


def _off_machine_providers() -> tuple:

    from ..core import tuning

    return tuning.names("off_machine_providers", _OFF_MACHINE_PROVIDERS)


_RANK = {"read": 0, "write": 1, "destructive": 2}
_SQL_MUTATION_RE = re.compile(r"(?is)^\s*(?:with\b.*?)?\b(?:delete|drop|truncate|update|alter|insert|replace|create)\b")
_MEMORY_SINKS = ("memory:", "ogr:", "postgres:", "TEMPORARY_OUTPUT")
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")



_OGR_DBNAME_RE = re.compile(r"^ogr:dbname='(?P<path>[^']+)'")


def static_danger(name: str) -> str:

    declared = spec(name)
    return declared.danger if declared is not None else UNKNOWN_DANGER


def looks_like_disk_path(value: Any) -> bool:

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


def _processing_replaces_file(args: dict, own_files: frozenset = frozenset()) -> bool:










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
        if looks_like_disk_path(candidate) and _target_taken(candidate, own_files):
            return True
    return False


def plugin_algorithm_danger(algorithm_id: str) -> str | None:






    algorithm_id = str(algorithm_id or "").strip()
    if not algorithm_id:
        return None
    if algorithm_id in paid_algorithms() or algorithm_id.startswith(_off_machine_providers()):
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


def _target_taken(path, own_files: frozenset = frozenset()) -> bool:








    if not isinstance(path, str) or not path.strip():
        return True
    try:


        file_part = os.path.expanduser(path.strip().split("|", 1)[0])
        if security.refused_share(file_part):


            return True
        if not os.path.exists(file_part):
            return False
        return os.path.realpath(file_part) not in own_files
    except Exception:  # noqa: BLE001
        return True


def sql_mutates(args: dict) -> bool:

    return bool(_SQL_MUTATION_RE.search(str(args.get("query") or args.get("sql") or "")))


def _declared_level(declared, args: dict, own_files: frozenset) -> str | None:

    if declared.reads_when is not None and declared.reads_when(args):
        return "read"
    if declared.replaces_file_at:
        return "destructive" if _target_taken(args.get(declared.replaces_file_at), own_files) else "write"
    if declared.processing is not None:
        algorithm, parameter_sets = declared.processing(args)
        if plugin_algorithm_danger(algorithm) or any(
            _processing_replaces_file({"algorithm_id": algorithm, "parameters": parameters}, own_files)
            for parameters in parameter_sets
        ):
            return "destructive"
    if declared.destructive_when is not None and declared.destructive_when(args):
        return "destructive"
    return None


def effective_danger(name: str, args: dict | None = None, own_files: frozenset = frozenset()) -> str:































    args = args if isinstance(args, dict) else {}
    level = static_danger(name)
    declared = spec(name)
    if declared is not None and declared.action_danger is not None:
        return declared.action_danger.get(str(args.get("action") or ""), level)
    by_spec = _declared_level(declared, args, own_files) if declared is not None else None
    if by_spec is not None:
        return by_spec
    for arg in _OPTIONAL_PATH_ARGS.get(name, ()):
        value = args.get(arg)





        if isinstance(value, str) and name in _APPENDS_EXTENSION and not os.path.splitext(value)[1]:
            value = value + _APPENDS_EXTENSION[name]



        if looks_like_disk_path(value) and _target_taken(value, own_files):
            return "destructive"
    return level

