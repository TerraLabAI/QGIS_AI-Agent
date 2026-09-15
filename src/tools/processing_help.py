# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""list_algorithms and get_algorithm_help: what the registry offers and what a parameter means."""
from __future__ import annotations

from qgis.core import QgsApplication

from ..core.policy import MAX_LIST_CHARS
from .processing_destinations import _is_optional, _provider_hint

_ALGORITHMS_CACHE: list[tuple] = []
_ALGORITHMS_CACHE_COUNT: int = -1


def _list_algorithms(args: dict) -> dict:
    global _ALGORITHMS_CACHE, _ALGORITHMS_CACHE_COUNT
    registry = QgsApplication.processingRegistry()
    algs = registry.algorithms()


    if len(algs) != _ALGORITHMS_CACHE_COUNT:
        _ALGORITHMS_CACHE = [(a.id(), a.displayName(), a.group()) for a in algs]
        _ALGORITHMS_CACHE_COUNT = len(algs)

    search = (args.get("search") or "").lower()
    limit = max(int(args.get("limit", 0) or 0), 0)

    results = []
    matched = 0
    size = 0
    over_budget = False
    for alg_id, name, group in _ALGORITHMS_CACHE:
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
        "total": len(_ALGORITHMS_CACHE),
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



    from .plugin_tools import skill_for_algorithm

    folder, skill = skill_for_algorithm(alg.id())
    if skill:
        out["plugin"] = folder
        out["how_to_use"] = skill
    return out

