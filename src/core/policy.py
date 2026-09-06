# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compatibility shim for the ported tool modules."""








from __future__ import annotations

import os
import shutil
import stat
import time
import uuid
from enum import Enum







from .host_platform import retry_file_op
from .limits import (  # noqa: F401  re-exported for the tool modules
    MAX_FEATURES_PER_CALL,
    RUN_MAX_SECONDS,
    RUN_MAX_STEPS,
)
from .logger import log, log_warning


def _qgis_profile_name() -> str:
    """Name of the active QGIS user profile, or '' outside a QGIS process."""
    try:
        from qgis.core import QgsApplication

        settings_dir = QgsApplication.qgisSettingsDirPath() or ""
    except Exception:
        return ""
    if not settings_dir:
        return ""
    return os.path.basename(os.path.normpath(settings_dir))


def _resolve_agent_home() -> str:
    """Per-instance state dir: ``~/.qgis_ai-agent``, one subtree per QGIS profile."""




    override = os.environ.get("QGIS_AI_AGENT_HOME", "").strip()
    if override:
        return os.path.normpath(os.path.expanduser(override))
    home = os.path.normpath(os.path.expanduser("~/.qgis_ai-agent"))
    profile = _qgis_profile_name()
    if profile and profile != "default":
        return os.path.join(home, "profiles", profile)
    return home


CURRENT_PROFILE = _qgis_profile_name()
AGENT_HOME = _resolve_agent_home()
AGENT_ROOT = os.path.normpath(os.path.expanduser("~/.qgis_ai-agent"))
AGENT_CACHE_DIR = os.path.join(AGENT_HOME, "cache")
AGENT_TMP_DIR = os.path.join(AGENT_HOME, "tmp")
AGENT_EXPORT_DIR = os.path.join(AGENT_HOME, "exports")




MAX_LIST_CHARS = 8_000


class ToolPolicyGroup(str, Enum):
    """Kept so ``policy_group=ToolPolicyGroup.X`` in the tool modules still compiles."""




    READ = "read"
    NAVIGATE = "navigate"
    PROJECT_WRITE = "project_write"
    NETWORK_IMPORT = "network_import"
    PROCESSING_SAFE = "processing_safe"
    SCOPED_FILE_WRITE = "scoped_file_write"
    DATA_MUTATION = "data_mutation"
    PROJECT_DESTRUCTIVE = "project_destructive"
    ADMIN_CODE = "admin_code"


def ensure_agent_directories():
    for path in (AGENT_HOME, AGENT_CACHE_DIR, AGENT_TMP_DIR, AGENT_EXPORT_DIR):
        os.makedirs(path, mode=0o700, exist_ok=True)


def create_managed_temp_dir(prefix: str = "job") -> str:
    ensure_agent_directories()
    directory = os.path.join(AGENT_TMP_DIR, f"{prefix}-{uuid.uuid4().hex[:12]}")
    os.makedirs(directory, mode=0o700, exist_ok=False)
    return directory




















PRUNE_MAX_AGE_DAYS = 7
PRUNE_MAX_TOTAL_BYTES = 5 * 1024 ** 3




_PRUNED_DIRS = (AGENT_TMP_DIR, AGENT_CACHE_DIR, AGENT_EXPORT_DIR)


def _dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _remove_scratch_entry(path: str) -> bool:
    """Delete one job folder (or loose file)."""







    failed = []

    def _on_error(func, target, _exc_info):
        try:
            retry_file_op(func, target)
            return
        except OSError:
            pass
        try:




            os.chmod(target, stat.S_IWRITE)
            retry_file_op(func, target)
        except OSError:
            failed.append(target)

    try:
        if os.path.isdir(path) and not os.path.islink(path):
            try:
                shutil.rmtree(path, onexc=lambda f, t, e: _on_error(f, t, e))
            except TypeError:
                shutil.rmtree(path, onerror=_on_error)
        else:
            retry_file_op(os.remove, path)
    except OSError as exc:
        log_warning(f"Scratch entry not removed ({path}): {exc}")
        return False
    if failed:
        log_warning(f"Scratch folder partly left behind: {len(failed)} entries under {path} "
                    "are held open by another program.")
        return False
    return True


def _prune_dir(base: str, max_age_days: float, max_total_bytes: int) -> dict:
    """Remove entries of ``base`` older than ``max_age_days``, then, if what is left still exceeds ``max_total_bytes``, remove the oldest of what."""





    result = {"removed": 0, "kept": 0, "kept_bytes": 0, "left_open": 0}
    try:
        names = os.listdir(base)
    except OSError:
        return result
    now = time.time()
    cutoff = now - max_age_days * 86400

    survivors: list[tuple[float, str, int, bool]] = []
    for name in names:
        path = os.path.join(base, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        size = _dir_bytes(path) if os.path.isdir(path) else os.path.getsize(path)
        if mtime < cutoff:
            if _remove_scratch_entry(path):
                result["removed"] += 1
                continue
            result["left_open"] += 1
            survivors.append((mtime, path, size, True))
        else:
            survivors.append((mtime, path, size, False))
    survivors.sort()
    total = sum(size for _mtime, _path, size, _tried in survivors)
    for _mtime, path, size, tried in survivors:
        if total <= max_total_bytes:
            break
        if tried:


            continue
        if _remove_scratch_entry(path):
            result["removed"] += 1
            total -= size
        else:
            result["left_open"] += 1
    result["kept"] = len(names) - result["removed"]
    result["kept_bytes"] = total
    return result


def prune_agent_scratch_dirs(max_age_days: float = PRUNE_MAX_AGE_DAYS,
                             max_total_bytes: int = PRUNE_MAX_TOTAL_BYTES) -> None:
    """Prune the plugin's own scratch folders, off the Qt main thread."""












    def work():
        for base in _PRUNED_DIRS:
            counts = _prune_dir(base, max_age_days, max_total_bytes)
            if counts["removed"] or counts["left_open"]:
                log(f"Scratch prune ({os.path.basename(base)}): removed {counts['removed']}, "
                    f"kept {counts['kept']} ({counts['kept_bytes'] / 1024 / 1024:.1f} MB), "
                    f"{counts['left_open']} left open.")

    def done(_result, error_text):
        if error_text:
            log_warning(f"Scratch prune failed: {error_text.splitlines()[0]}")

    try:
        from . import background

        task = background.run_off_thread("AI Agent: prune scratch folders", work, done)
    except Exception:  # noqa: BLE001 - no background module (plain-python tests): run inline
        task = None
    if task is None:
        work()


def get_security_context() -> dict:
    """Same keys the internal plugin put in ``get_project_context``, always open."""




    return {
        "mode": "no_limits",
        "label": "No limits",
        "description": "Every tool is available. Destructive calls ask in the chat panel first.",
        "sessionScopedNoLimits": False,
        "persistentMode": "no_limits",
    }
