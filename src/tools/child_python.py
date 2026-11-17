# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import json
import os
import shutil
import signal
import site
import sys

from ..core.host_platform import IS_WINDOWS
from ..core.logger import log_warning
from ..core.policy import AGENT_HOME


def read_json(path: str) -> dict:

    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _tail(path: str, limit: int = 1_500) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - limit))
            return " ".join(handle.read().decode("utf-8", "replace").split())[-limit:]
    except OSError:
        return ""




_WINDOWS_EXITS = {
    0xC0000005: "access violation",
    0xC0000409: "fail-fast: abort() in native code, or a stack buffer overrun",
    0xC0000374: "heap corruption",
    0xC00000FD: "stack overflow",
    0xC0000017: "out of memory",
    0xC000001D: "illegal instruction",
    0xC0000135: "a DLL was not found",
    0xC0000139: "a DLL lacks a function another one needs",
    0xC000007B: "a DLL built for another architecture",
    0xC0000142: "a DLL failed to initialise",
    0xC000013A: "stopped by Ctrl+C or a closed console",
    0xC0000420: "assertion failure",
    0xE06D7363: "uncaught C++ exception",
}


def exit_name(code) -> str:

    if not isinstance(code, int) or 0 <= code < 0xC0000000:
        return ""
    if code < 0:
        try:
            return signal.Signals(-code).name
        except ValueError:
            return f"signal {-code}"
    name = _WINDOWS_EXITS.get(code & 0xFFFFFFFF)
    return f"0x{code & 0xFFFFFFFF:08X}" + (f" {name}" if name else "")


def ended_without_result(code, log_path: str, limit: int) -> str:

    name = exit_name(code)
    tail = _tail(log_path, limit)
    return ("The separate QGIS ended without a result" + (f" (exit code {name})" if name else "")
            + (f": {tail}" if tail else "."))


def usable_python(path: str) -> bool:







    if not path or not os.path.isfile(path):
        return False
    parts = os.path.abspath(path).lower().replace("\\", "/").split("/")
    return "windowsapps" not in parts


def _qgis_python() -> str:

    from qgis.core import QgsApplication

    versioned = f"python{sys.version_info.major}.{sys.version_info.minor}"
    app_dir = QgsApplication.applicationDirPath()
    candidates = [
        sys.executable,
        os.path.join(app_dir, versioned),
        os.path.join(app_dir, "python3"),
        os.path.join(app_dir, "python.exe"),



        os.path.join(sys.prefix, "python.exe"),
        os.path.join(sys.exec_prefix, "python.exe"),
    ]
    for candidate in candidates:
        if usable_python(candidate) and os.path.basename(candidate).lower().startswith("python"):
            return candidate
    for name in (versioned, "python3", "python"):
        found = shutil.which(name)
        if usable_python(found):
            return found
    return ""







_QT_PLATFORM = "windows" if IS_WINDOWS else "offscreen"


def _child_environment(python_executable: str, work_dir: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "QT_QPA_PLATFORM": _QT_PLATFORM,
        "QGIS_CUSTOM_CONFIG_PATH": os.path.join(work_dir, "qgis-profile"),
        "QGIS_AI_AGENT_HOME": os.path.join(work_dir, "agent-home"),
        "TMPDIR": work_dir,
        "TMP": work_dir,
        "TEMP": work_dir,



        "PYTHONIOENCODING": "utf-8",
    })
    marker = f"{os.sep}Contents{os.sep}MacOS{os.sep}"
    if marker in python_executable:
        contents = python_executable.split(marker, 1)[0] + f"{os.sep}Contents"
        frameworks = os.path.join(contents, "Frameworks")
        qgis_resources = os.path.join(contents, "Resources", "qgis")
        env.update({
            "PYTHONHOME": frameworks,
            "PYTHONNOUSERSITE": "1",
            "DYLD_FRAMEWORK_PATH": frameworks,
            "PROJ_DATA": os.path.join(qgis_resources, "proj"),
            "PYTHONPATH": os.path.join(qgis_resources, "python", "plugins"),
        })
    elif IS_WINDOWS:



        try:
            import qgis

            qgis_python = os.path.dirname(os.path.dirname(os.path.abspath(qgis.__file__)))
        except Exception:  # noqa: BLE001
            qgis_python = ""
        if qgis_python:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join([qgis_python] + ([existing] if existing else []))
    return env


_PYTHON: list[str] = []


def python_for_child() -> str:





    if _PYTHON:
        return _PYTHON[0]
    candidates: list[str] = []
    if os.path.basename(sys.executable or "").lower().startswith("python"):
        candidates.append(sys.executable)
    versioned = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for base in (sys.prefix, sys.exec_prefix):
        candidates.extend((
            os.path.join(base, "python.exe"),
            os.path.join(base, "bin", versioned),
            os.path.join(base, "bin", "python3"),
        ))
    try:
        from qgis.core import QgsApplication

        app_dir = QgsApplication.applicationDirPath()
    except Exception:  # noqa: BLE001
        app_dir = ""
    if app_dir:
        candidates.extend((
            os.path.join(app_dir, versioned),
            os.path.join(app_dir, "python3"),
            os.path.join(app_dir, "python.exe"),
        ))
    for name in (versioned, "python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    answer = next((path for path in candidates if usable_python(path)), "")
    _PYTHON.append(answer)
    return answer


def start_refused(exc: OSError) -> str:






    _PYTHON[:] = [""]
    log_warning(f"This computer refused to start QGIS's Python, separate processes are off: {exc}")
    return f"This computer refused to start the Python interpreter shipped with QGIS ({exc})."


def _is_user_site(entry: str) -> bool:








    target = os.path.normcase(os.path.abspath(entry))
    roots: list[str] = []
    try:
        user_site = site.getusersitepackages()
    except Exception:  # noqa: BLE001
        user_site = ""
    if isinstance(user_site, str) and user_site:
        roots.append(user_site)
    user_base = getattr(site, "USER_BASE", None)
    if isinstance(user_base, str) and user_base:
        roots.append(user_base)
    for root in roots:
        root = os.path.normcase(os.path.abspath(root))
        if target == root or target.startswith(root + os.sep):
            return True
    return False


def environment(python: str, work_dir: str) -> dict:

    if "qgis" in python.lower():
        environment = _child_environment(python, work_dir)
    else:
        environment = dict(os.environ)
        environment["QT_QPA_PLATFORM"] = _QT_PLATFORM
    environment["QGIS_CUSTOM_CONFIG_PATH"] = os.path.join(work_dir, "qgis-profile")
    environment["QGIS_AI_AGENT_HOME"] = AGENT_HOME
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"


    environment["PYTHONIOENCODING"] = "utf-8"
    environment.pop("PYTHONSTARTUP", None)




    inherited = [entry for entry in sys.path if isinstance(entry, str) and entry and os.path.exists(entry)]
    application = [entry for entry in inherited if not _is_user_site(entry)]
    user_site = [entry for entry in inherited if _is_user_site(entry)]
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        application + user_site + ([existing] if existing else []))
    return environment
