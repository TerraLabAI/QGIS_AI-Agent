# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""list_algorithms and get_algorithm_help: what the registry offers and what a parameter means."""
from __future__ import annotations

from qgis.core import QgsApplication

from ..core.policy import MAX_LIST_CHARS
from .processing_destinations import _is_optional, _provider_hint


_algorithms: dict = {"rows": [], "count": -1}


def _list_algorithms(args: dict) -> dict:
    registry = QgsApplication.processingRegistry()
    algs = registry.algorithms()


    if len(algs) != _algorithms["count"]:
        _algorithms["rows"] = [(a.id(), a.displayName(), a.group()) for a in algs]
        _algorithms["count"] = len(algs)
    rows = _algorithms["rows"]

    search = (args.get("search") or "").lower()
    limit = max(int(args.get("limit", 0) or 0), 0)

    results = []
    matched = 0
    size = 0
    over_budget = False
    for alg_id, name, group in rows:
        if search and search not in alg_id.lower() and search not in name.lower():
            continue
        matched += 1
        if over_budget or (limit and len(results) >= limit):
            continue
        entry = {"id": alg_id, "name": name, "group": group}


        size += len(alg_id) + len(name) + len(group) + 40
        if results and size > MAX_LIST_CHARS:
            over_budget = True
            continue
        results.append(entry)

    out = {
        "total": len(rows),
        "matched": matched,
        "count": len(results),
        "algorithms": results,
    }
    if matched > len(results):
        out["omitted"] = matched - len(results)
    return out



def _parameter_help(param) -> dict:
    """Serialize one Processing parameter, including enum integer meanings."""







    answer = {
        "name": param.name(),
        "description": param.description(),
        "type": param.type(),
        "required": not _is_optional(param),
        "default": param.defaultValue(),
    }
    if getattr(param, "isDestination", lambda: False)():


        answer["destination"] = True
    if str(param.type()).lower() == "enum":
        try:
            choices = list(param.options())
        except (AttributeError, TypeError, RuntimeError):
            choices = []
        if choices:
            answer["options"] = [
                {"value": index, "label": str(label)}
                for index, label in enumerate(choices)
            ]
    return answer


def _get_algorithm_help(args: dict) -> dict:
    algorithm_id = args["algorithm_id"]
    alg = QgsApplication.processingRegistry().algorithmById(algorithm_id)
    if not alg:
        return {"_error": f"Algorithm not found: {algorithm_id}", **_provider_hint(algorithm_id)}

    params = [_parameter_help(param) for param in alg.parameterDefinitions()]

    out = {
        "id": alg.id(),
        "name": alg.displayName(),
        "description": alg.shortDescription() or alg.shortHelpString(),
        "parameters": params,
    }



    example: dict = {}
    for param in params:
        if param.get("destination"):
            example[param["name"]] = "TEMPORARY_OUTPUT"
        elif param["required"]:
            example[param["name"]] = f"<{param['type']}>"
    out["required"] = [p["name"] for p in params if p["required"] and not p.get("destination")]
    out["example_call"] = {"tool": "run_processing",
                           "arguments": {"algorithm_id": alg.id(), "parameters": example,
                                         "output_name": "<the layer name for the user>"}}



    from .plugin_tools import skill_for_algorithm

    folder, skill = skill_for_algorithm(alg.id())
    if skill:
        out["plugin"] = folder
        out["how_to_use"] = skill
    return out

