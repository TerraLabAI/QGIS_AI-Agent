# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

import qgis.utils
from qgis.core import QgsApplication

from . import catalog
from .served_state import PRODUCT_ID, SITE_URL


@dataclass(frozen=True)
class Sibling:
    product_id: str
    label: str


    folders: tuple[str, ...]

    released_folder: str


AI_EDIT = Sibling(
    product_id="ai-edit",
    label="AI Edit",
    folders=("QGIS_AI-Edit-Team", "QGIS_AI-Edit", "AI_Edit", "ai_edit"),
    released_folder="AI_Edit",
)
AI_SEGMENTATION = Sibling(
    product_id="ai-segmentation",
    label="AI Segmentation",
    folders=(
        "QGIS_AI-Segmentation-Team",
        "QGIS_AI-Segmentation",
        "QGIS_AI_Segmentation_Team",
        "AI_Segmentation",
        "ai_segmentation",
    ),
    released_folder="AI_Segmentation",
)
ALL = (AI_EDIT, AI_SEGMENTATION)


def by_product(product_id: str) -> Sibling | None:
    return next((sibling for sibling in ALL if sibling.product_id == product_id), None)


def find(sibling: Sibling) -> tuple[str | None, Any]:






    loaded = getattr(qgis.utils, "plugins", None) or {}
    for key in sibling.folders:
        inst = loaded.get(key)
        if inst is not None:
            return key, inst
    for key, inst in list(loaded.items()):
        try:
            title = str(qgis.utils.pluginMetadata(key, "name") or "")
        except Exception:  # noqa: BLE001
            title = ""
        if title.startswith(sibling.label) and hasattr(inst, "mcp_api"):
            return key, inst
    for key, inst in list(loaded.items()):
        if inst is not None and key.startswith(sibling.folders):
            return key, inst
    return None, None


def plugin_dirs() -> list[str]:

    dirs = [path for path in (getattr(qgis.utils, "plugin_paths", None) or []) if isinstance(path, str)]
    try:
        dirs.append(os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins"))
    except Exception:  # nosec B110
        pass
    dirs.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    return dirs


def installed_folder(folders: tuple[str, ...]) -> str | None:

    available = set(getattr(qgis.utils, "available_plugins", None) or [])
    folder = next((key for key in folders if key in available), None)
    if folder is not None:
        return folder
    return next((key for base in plugin_dirs() for key in folders
                 if os.path.isfile(os.path.join(base, key, "metadata.txt"))), None)


def installed(sibling: Sibling) -> bool:

    started = set(getattr(qgis.utils, "plugins", None) or {}) | set(getattr(qgis.utils, "active_plugins", None) or [])
    return any(name in started for name in sibling.folders) or installed_folder(sibling.folders) is not None


def api_version(plugin) -> int | None:
    api = getattr(plugin, "mcp_api", None)
    if api is None:
        return None


    version = getattr(sys.modules.get(type(api).__module__), "API_VERSION", None)
    if isinstance(version, int):
        return version
    try:
        reported = api.capabilities()
        if isinstance(reported, dict) and isinstance(reported.get("api_version"), int):
            return reported["api_version"]
    except Exception:  # nosec B110
        pass
    return None


def _tagged(url: str, content: str) -> str:






    if not url or not url.startswith(SITE_URL):
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source=qgis&utm_medium=plugin&utm_campaign={PRODUCT_ID}&utm_content={content}"


def sibling_info(product_id: str) -> dict | None:






    sibling = by_product(product_id)
    if sibling is None:
        return None
    served = catalog.plugin_roster().get(product_id) or {}
    return {
        "folder": sibling.released_folder,
        "product_id": product_id,
        "name": served.get("name") or "",
        "label": served.get("label") or "",
        "url": _tagged(served.get("url") or "", "cross_promo"),
        "tutorial_url": _tagged(served.get("tutorial_url") or "", "cross_promo_guide"),
        "thumbnail_url": served.get("thumbnail_url") or "",
    }


def is_sibling_installed(product_id: str) -> bool:

    sibling = by_product(product_id)
    return sibling is not None and find(sibling)[1] is not None
