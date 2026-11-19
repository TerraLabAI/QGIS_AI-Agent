# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import functools
import json
import os

from ..core import siblings

V7 = 7
CONTRACT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "contracts", "vectorize_contract.json")


@functools.lru_cache(maxsize=1)
def vectorize_contract() -> dict:
    with open(CONTRACT_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def v7_api(plugin):

    if plugin is None or (siblings.api_version(plugin) or 0) < V7:
        return None
    api = getattr(plugin, "mcp_api", None)
    return api if callable(getattr(api, "vectorize_quote", None)) else None


def request_from_args(args: dict, zone_wkt: str, holds_zone: bool) -> dict:


    target: dict = {}
    if (args.get("object_class") or "").strip():
        target["class"] = args["object_class"].strip()
    if args.get("accept_weak_class") is not None:
        target["accept_weak"] = bool(args["accept_weak_class"])
    if args.get("exemplars") is not None:
        target["exemplars"] = list(args["exemplars"])
    request: dict = {"target": target, "output": {"review": True, "caller": "ai_agent"}}
    if args.get("capability"):
        request["capability"] = args["capability"]
    if args.get("output_geometry"):
        request["output"]["geometry"] = args["output_geometry"]
    if args.get("layer_name"):
        request["source"] = {"layer": args["layer_name"]}
    if zone_wkt:
        request["zone"] = {"wkt": zone_wkt}
    elif holds_zone:
        request["zone"] = {"use_zone": True}
    settings: dict = {}
    for key in ("confidence", "detail"):
        if args.get(key) is not None:
            settings[key] = args[key]
    if args.get("refine"):
        settings["refine"] = {"ortho" if key == "right_angles" else key: value
                              for key, value in args["refine"].items()}
    if settings:
        request["settings"] = settings
    return request


def output_geometries(api) -> list[str]:

    try:
        caps = api.vectorize_capabilities()
        objects = next(c for c in caps.get("capabilities", []) if c.get("kind") == "objects")
        return list(objects.get("output_geometries") or ["polygon"])
    except Exception:  # noqa: BLE001
        return ["polygon"]


def confidence_band(api) -> tuple[float, float]:


    try:
        caps = api.vectorize_capabilities()
        objects = next(c for c in caps.get("capabilities", []) if c.get("kind") == "objects")
        band = objects["settings_schema"]["confidence"]
        return float(band["minimum"]), float(band["maximum"])
    except Exception:  # noqa: BLE001
        return 0.05, 0.95


def result_extras(api, run_id: str | None) -> dict:

    if api is None or not run_id:
        return {}
    try:
        status = api.vectorize_status(run_id=run_id)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(status, dict) or not status.get("ok"):
        return {}
    extras = {"state": status.get("state")}
    result = status.get("result") or {}
    for key in ("coverage", "quality", "output_geometry"):
        if result.get(key):
            extras[key] = result[key]
    return extras
