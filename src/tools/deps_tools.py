# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Optional Python dependency discovery and self-install tools."""












from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import site
import sys
import tempfile
import time
import uuid

from qgis.PyQt.QtCore import QCoreApplication, QThread

from ..core.logger import log
from ..core.policy import AGENT_HOME
from ..core.tool_registry import Tool, ToolRegistry





OPTIONAL_DEPENDENCIES = {
    "earthengine": {
        "package": "earthengine-api",
        "import_name": "ee",
        "unlocks": (
            "Google Earth Engine tools (add_gee_dataset, gee_compute_index, gee_zonal_stats, "
            "initialize_earth_engine). search_gee_catalog is answered by the server and needs "
            "no install."
        ),
        "post_install": (
            "Run `earthengine authenticate` once in a terminal, then reload the plugin "
            "(QGIS plugin manager) so the Earth Engine tools register."
        ),
    },
}

_INSTALL_TASKS: dict[str, dict] = {}
_WORKERS: list = []




_STALE_RUNNING_S = 15 * 60


_SWEEP_FINISHED_AFTER_S = 30 * 60







_INFLIGHT_PROPERTY = "terralab_ai_agent_dep_install"





def _is_installed(import_name: str) -> bool:
    """True if the module can be imported in the running interpreter."""
    try:
        return importlib.util.find_spec(import_name) is not None
    except Exception:  # nosec B110 - site refresh is optional
        return False


def _resolve_feature(args: dict):
    """Map args (feature or package) to a whitelisted (feature_key, spec) pair."""





    allowed = sorted(OPTIONAL_DEPENDENCIES.keys())
    feature = (args.get("feature") or "").strip()
    if feature:
        spec = OPTIONAL_DEPENDENCIES.get(feature)
        if not spec:
            return None, {"_error": f"Unknown feature '{feature}'. Allowed features: {allowed}",
                          "code": "INVALID_ARGS",
                          "suggestion": f"Pass one of: {allowed}."}
        return feature, spec

    package = (args.get("package") or "").strip()
    if package:
        for key, spec in OPTIONAL_DEPENDENCIES.items():
            if spec["package"] == package:
                return key, spec
        return None, {
            "_error": f"Package '{package}' is not whitelisted. Allowed features: {allowed}",
            "code": "INVALID_ARGS",
            "suggestion": "Pass 'feature' instead; the agent installs only the packages in that table.",
        }

    return None, {"_error": f"Provide 'feature' (one of {allowed}) or a whitelisted 'package'.",
                  "code": "INVALID_ARGS",
                  "suggestion": f"check_optional_dependencies lists what is installed; features are {allowed}."}


def _pip_failure_message(rc: int, log_tail: str) -> str:
    """What pip's exit code means, in the one case where the number misleads."""









    held = ("winerror 5" in log_tail.lower() or "winerror 32" in log_tail.lower()
            or "access is denied" in log_tail.lower()
            or "used by another process" in log_tail.lower())
    if held:
        return ("This package is already partly installed and QGIS is holding one of its "
                "files open, so pip could not replace it. Close and reopen QGIS, then "
                "install again.")
    return f"pip exited with code {rc}."


def deps_dir() -> str:
    """Where optional dependencies are installed, outside the QGIS profile."""









    return os.path.join(AGENT_HOME, "site-packages",
                        f"py{sys.version_info[0]}.{sys.version_info[1]}")


def _refresh_user_site():
    """Make a fresh install importable in the running process."""
    try:
        site.main()
    except Exception:  # nosec B110 - site refresh is optional
        pass
    for path in (deps_dir(), _legacy_user_site()):
        if path and path not in sys.path:
            sys.path.append(path)
    importlib.invalidate_caches()


def _ensure_deps_on_path() -> None:
    """Put the folder on sys.path at load, so last session's install imports."""
    for path in (deps_dir(), _legacy_user_site()):
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)


def _legacy_user_site() -> str:
    """The old --user location, kept on the path so an earlier install still imports."""
    try:
        return site.getusersitepackages() or ""
    except Exception:  # nosec B110 - the user site is optional
        return ""





def _application():
    """The running application object (QgsApplication under QGIS), or None."""





    try:
        return QCoreApplication.instance()
    except Exception:  # nosec B110 - no application means nothing to record on
        return None


def _set_inflight(package: str) -> None:
    """Record on the application that `package` is still being installed."""
    app = _application()
    if app is None:
        return
    try:
        app.setProperty(_INFLIGHT_PROPERTY, [str(package), time.monotonic()])
    except Exception as exc:  # noqa: BLE001 - unload never fails on housekeeping
        log(f"Running install not recorded across the reload: {exc}")


def _clear_inflight() -> None:
    app = _application()
    if app is None:
        return
    with contextlib.suppress(Exception):
        app.setProperty(_INFLIGHT_PROPERTY, None)


def _inflight_package() -> str:
    """The package an install from before a reload is still writing, if any."""





    app = _application()
    if app is None:
        return ""
    try:
        recorded = app.property(_INFLIGHT_PROPERTY)
        package, started = str(recorded[0]), float(recorded[1])
    except Exception:  # nosec B110 - nothing recorded, or not our shape
        return ""
    if time.monotonic() - started > _STALE_RUNNING_S:
        _clear_inflight()
        return ""
    return package


def shutdown() -> None:
    """Plugin unload: hand over what a running install has to outlive."""











    app = _application()
    for worker in list(_WORKERS):
        try:
            if not worker.isRunning():
                continue
            if app is not None:
                worker.setParent(app)
            worker.finished.connect(_clear_inflight)
            _set_inflight(getattr(worker, "_package", ""))
            log(f"Dependency install still running at unload: {getattr(worker, '_package', '?')}")
        except (RuntimeError, TypeError) as exc:
            log(f"Install worker not handed over on unload: {exc}")
    _WORKERS.clear()
    _INSTALL_TASKS.clear()





def register_deps_tools(registry: ToolRegistry):
    _ensure_deps_on_path()
    registry.register(Tool(
        name="check_optional_dependencies",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_check,
    ))

    registry.register(Tool(
        name="install_dependency",
        input_schema={
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",




                    "enum": sorted(OPTIONAL_DEPENDENCIES),
                },
                "package": {"type": "string"},
            },
            "required": [],
        },
        handler=_install,
    ))

    registry.register(Tool(
        name="install_dependency_status",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        handler=_install_status,
    ))

    log("Optional-dependency tools registered")







_USER_CONTENT_PROVIDERS = frozenset({"model", "project", "script"})


def _qgis_capabilities() -> dict:
    """What this QGIS install can do, beside what pip can add to it."""














    out: dict = {}
    try:
        from qgis.core import QgsApplication

        registry = QgsApplication.processingRegistry()
        counts: dict = {}
        for alg in registry.algorithms():
            prefix = alg.id().split(":", 1)[0]
            counts[prefix] = counts.get(prefix, 0) + 1
        registered = sorted(p.id() for p in registry.providers())
        out["processing_providers"] = {name: counts.get(name, 0) for name in registered}





        empty = [name for name in registered
                 if not counts.get(name) and name not in _USER_CONTENT_PROVIDERS]
        if empty:
            out["providers_registered_but_empty"] = empty
        out["algorithms_total"] = sum(counts.values())
    except Exception as exc:  # noqa: BLE001 - no QGIS, or a registry that will not answer
        out["processing_providers"] = f"not readable: {exc}"
    try:
        from qgis.core import QgsProviderRegistry

        out["data_providers"] = sorted(QgsProviderRegistry.instance().providerList())
    except Exception as exc:  # noqa: BLE001
        out["data_providers"] = f"not readable: {exc}"
    return out


def _check(args: dict) -> dict:
    deps = []
    installed_count = 0
    for feature, spec in OPTIONAL_DEPENDENCIES.items():
        installed = _is_installed(spec["import_name"])
        if installed:
            installed_count += 1
        deps.append({
            "feature": feature,
            "package": spec["package"],
            "installed": installed,
            "unlocks": spec["unlocks"],
            "post_install": spec["post_install"],
        })

    total = len(OPTIONAL_DEPENDENCIES)
    capabilities = _qgis_capabilities()
    out: dict = {
        "dependencies": deps,
        "qgis_capabilities": capabilities,
        "_summary": f"{installed_count} of {total} optional features installed",
    }
    empty = capabilities.get("providers_registered_but_empty")
    if empty:
        names = ", ".join(empty)
        out["_summary"] += (
            f"; {names} {'is' if len(empty) == 1 else 'are'} registered with no algorithms, "
            f"so an id under {'it' if len(empty) == 1 else 'them'} resolves to nothing here"
        )
    missing = [d["feature"] for d in deps if not d["installed"]]
    if missing:
        out["_next"] = (
            f"Call install_dependency {{feature:'{missing[0]}'}} to add it, then reload the plugin."
        )
    return out


class _InstallWorker(QThread):
    """Runs in-process pip to install one package, recording timed progress."""

    def __init__(self, task: dict, package: str, import_name: str):
        super().__init__()
        self._task = task
        self._package = package
        self._import_name = import_name

    def _record(self, msg: str):
        t = self._task
        t["message"] = msg
        t["timeline"].append({"t": round(time.monotonic() - t["t0"], 1), "msg": msg})

    def run(self):
        t = self._task
        try:
            self._record("starting")
            try:
                from pip._internal.cli.main import main as _pip_main
            except Exception as e:
                t["status"] = "error"
                t["error"] = (
                    f"In-process pip unavailable ({e}). Install manually: "
                    f"python -m pip install --user {self._package}"
                )
                self._record("failed: pip unavailable")
                return

            self._record("pip running")





            rc = 1
            log_fd, log_path = tempfile.mkstemp(prefix="qgis_ai_agent_pip_", suffix=".log")
            os.close(log_fd)
            try:
                target = deps_dir()
                os.makedirs(target, exist_ok=True)


                rc = _pip_main([
                    "install", "--target", target, "--upgrade",
                    "--disable-pip-version-check",
                    "--no-input", "-q", "--log", log_path, self._package,
                ])
            except SystemExit as e:
                rc = int(e.code) if isinstance(e.code, int) else 1
            except Exception as e:
                t["error"] = f"pip raised: {e}"
                rc = 1

            t["return_code"] = rc
            try:


                with open(log_path, encoding="utf-8", errors="replace") as fh:
                    t["output_tail"] = fh.read()[-2000:]
            except OSError:
                t["output_tail"] = ""
            finally:
                with contextlib.suppress(OSError):
                    os.remove(log_path)

            self._record("verifying")
            _refresh_user_site()
            installed = _is_installed(self._import_name)

            if rc == 0 and installed:
                t["status"] = "complete"
                self._record("done")
            else:
                t["status"] = "error"
                if not t.get("error"):
                    if rc != 0:
                        t["error"] = _pip_failure_message(rc, t.get("output_tail") or "")
                    else:
                        t["error"] = f"pip finished but '{self._import_name}' still does not import."
                self._record("failed")
        except Exception as e:
            import traceback
            t["status"] = "error"
            t["error"] = f"{e}\n{traceback.format_exc()}"
            self._record("failed: unexpected error")
        finally:
            t["finished_at"] = time.time()


def _sweep_finished_tasks():
    """Drop terminal task entries finished more than 30 minutes ago, so _INSTALL_TASKS does not grow unbounded across a session."""

    now = time.time()
    stale = [
        tid for tid, t in _INSTALL_TASKS.items()
        if t["status"] != "running" and t.get("finished_at") and (now - t["finished_at"]) > _SWEEP_FINISHED_AFTER_S
    ]
    for tid in stale:
        _INSTALL_TASKS.pop(tid, None)


def _install(args: dict) -> dict:
    feature, resolved = _resolve_feature(args)
    if feature is None:
        return resolved
    spec = resolved
    package = spec["package"]
    import_name = spec["import_name"]

    if _is_installed(import_name):
        return {"already_installed": True, "feature": feature, "package": package}




    try:
        os.makedirs(deps_dir(), exist_ok=True)
    except OSError as exc:
        return {
            "_error": f"Cannot create the folder optional packages install into: {exc}",
            "suggestion": "Install the package into this interpreter's environment yourself, "
                          f"then reload the plugin: pip install {package}",
        }

    orphan = _inflight_package()
    if orphan:
        return {
            "_error": f"An install of {orphan} started before the plugin was reloaded is still "
                      "running in this QGIS session.",
            "suggestion": "Wait for it to finish, then reload the plugin. Two pip runs writing the "
                          "same site-packages at once leave a half-installed package.",
        }

    _sweep_finished_tasks()
    now = time.monotonic()
    for tid, t in _INSTALL_TASKS.items():
        if t["status"] != "running":
            continue



        if now - t.get("t0", now) > _STALE_RUNNING_S:
            t["status"] = "error"
            t["error"] = "timed out"
            t["finished_at"] = time.time()
            continue
        return {"_error": f"Install task {tid} is already running. Poll it or wait."}

    task_id = "depinstall-" + uuid.uuid4().hex[:8]
    task = {
        "status": "running", "message": "queued", "error": None,
        "feature": feature, "package": package, "import_name": import_name,
        "timeline": [], "return_code": None, "output_tail": None,
        "t0": time.monotonic(), "started_at": time.time(), "finished_at": None,
    }
    _INSTALL_TASKS[task_id] = task
    worker = _InstallWorker(task, package, import_name)
    _WORKERS.append(worker)
    worker.finished.connect(lambda w=worker: _WORKERS.remove(w) if w in _WORKERS else None)
    worker.start()
    log(f"Dependency install task {task_id} started ({package})")
    return {
        "task_id": task_id,
        "status": "running",
        "package": package,
        "hint": "Poll install_dependency_status {task_id}. Then reload the plugin so the new tools register.",
    }


def _install_status(args: dict) -> dict:
    t = _INSTALL_TASKS.get(args.get("task_id", ""))
    if not t:
        return {"_error": "Unknown task_id", "known": list(_INSTALL_TASKS.keys())}

    elapsed = round((t["finished_at"] or time.time()) - t["started_at"], 1)
    installed_now = _is_installed(t["import_name"])
    out: dict = {
        "status": t["status"],
        "package": t["package"],
        "elapsed_s": elapsed,
        "return_code": t["return_code"],
        "installed_now": installed_now,
        "timeline": t["timeline"][-15:],
    }
    if t.get("output_tail"):
        out["output_tail"] = t["output_tail"]
    if t["error"]:
        out["error"] = t["error"]
    if t["status"] == "complete":
        spec = OPTIONAL_DEPENDENCIES.get(t["feature"], {})
        out["post_install"] = spec.get("post_install", "")
        out["_next"] = "Reload the plugin so the new tools register."
    return out
