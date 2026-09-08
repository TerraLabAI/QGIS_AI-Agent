# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read the AI Segmentation install state, so the agent can explain the install."""









from __future__ import annotations

import importlib
import os

from qgis.core import QgsSettings

from ..core.logger import log
from ..core.tool_registry import Tool, ToolRegistry
from ._widgets import AI_SEGMENT_KEYS


AISEG_PACKAGES = list(AI_SEGMENT_KEYS)




def _seg_package() -> str | None:
    import qgis.utils
    for key in AISEG_PACKAGES:
        if key in qgis.utils.plugins or key in getattr(qgis.utils, "available_plugins", []):
            return key
    return None


def _seg_core(module: str):
    pkg = _seg_package()
    if not pkg:
        raise RuntimeError("AI Segmentation plugin not found in the QGIS plugins directory.")
    return importlib.import_module(f"{pkg}.src.core.{module}")


def _cache_dir() -> str:




    return os.path.normpath(
        os.environ.get("AI_SEGMENTATION_CACHE_DIR") or os.path.expanduser("~/.qgis_ai_segmentation")
    )




_DIR_WALK_CAP = 20_000


def _dir_size_mb(path: str) -> float:
    total = 0
    seen = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            seen += 1
            if seen > _DIR_WALK_CAP:
                return round(total / (1024 * 1024), 1)
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return round(total / (1024 * 1024), 1)





def register_aiseg_install_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="ai_segment_install_status",
        input_schema={
            "type": "object",
            "properties": {
                "include_packages": {
                    "type": "boolean",
                },
                "include_sizes": {
                    "type": "boolean",
                },
            },
            "required": [],
        },
        handler=_install_status,
    ))

    log("AI Segmentation install-status tool registered")





def _install_status(args: dict) -> dict:
    include_sizes = bool(args.get("include_sizes", False))
    cache = _cache_dir()
    components = {}
    for name, sub in [("python_standalone", "python_standalone"), ("uv", "uv"), ("checkpoints", "checkpoints")]:
        p = os.path.join(cache, sub)
        present = os.path.isdir(p)
        entry = {"present": present}
        if include_sizes:
            entry["size_mb"] = _dir_size_mb(p) if present else 0
        components[name] = entry
    venvs = []
    if os.path.isdir(cache):
        for entry in sorted(os.listdir(cache)):
            if entry.startswith("venv_py"):
                p = os.path.join(cache, entry)
                venv_info = {"name": entry}
                if include_sizes:
                    venv_info["size_mb"] = _dir_size_mb(p)
                venvs.append(venv_info)
    components["venvs"] = venvs

    out: dict = {
        "cache_dir": cache,
        "components": components,
        "install_marker_present": os.path.exists(os.path.join(cache, "install_in_progress")),
    }

    try:
        vm = _seg_core("venv_manager")
        ready, msg = vm.get_venv_status()
        out["venv_status"] = {"ready": ready, "message": msg}
    except Exception as e:
        out["venv_status"] = {"_error": str(e)}

    try:
        s = QgsSettings()
        out["account"] = {
            "key_present": bool(s.value("AISegmentation/activation_key", "")),
            "activated": str(s.value("AISegmentation/activated", "")) in ("true", "True", "1"),
            "tos_accepted": str(s.value("AISegmentation/tos_accepted", "")) in ("true", "True", "1"),
        }
    except Exception as e:
        out["account"] = {"_error": str(e)}

    import sys
    out["torch_imported_in_process"] = "torch" in sys.modules

    if args.get("include_packages") and venvs:
        out["packages"] = _venv_packages(cache, venvs[-1]["name"])
    return out


def _venv_packages(cache: str, venv_name: str) -> list[dict]:
    import glob
    import re
    site = glob.glob(os.path.join(cache, venv_name, "lib", "python*", "site-packages"))
    if not site:
        site = [os.path.join(cache, venv_name, "Lib", "site-packages")]
    if not os.path.isdir(site[0]):
        return []
    sp = site[0]
    packages = []
    for di in sorted(os.listdir(sp)):
        m = re.match(r"(.+)-([\d.]+(?:[a-z0-9.]*))\.dist-info$", di)
        if m:
            packages.append({"name": m.group(1), "version": m.group(2)})
    sizes = []
    for entry in os.listdir(sp):
        p = os.path.join(sp, entry)
        if os.path.isdir(p) and not entry.endswith(".dist-info"):
            sizes.append((entry, _dir_size_mb(p)))
    sizes.sort(key=lambda t: -t[1])
    return [{"distributions": packages, "top_dirs_mb": [{"dir": d, "mb": m} for d, m in sizes[:20]]}]
