# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

























from __future__ import annotations

import functools
import ipaddress
import ntpath
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
from typing import Any

from .host_platform import IS_WINDOWS
from .policy import AGENT_CACHE_DIR, AGENT_EXPORT_DIR, AGENT_HOME, AGENT_ROOT, AGENT_TMP_DIR


STRICT_WRITE_ROOTS = False

_HOME = os.path.normpath(os.path.expanduser("~"))
_PLUGIN_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_PLUGIN_RESOURCES = os.path.join(_PLUGIN_DIR, "resources")


_DENY_READ_DIRS = [
    os.path.join(_HOME, ".ssh"), os.path.join(_HOME, ".aws"), os.path.join(_HOME, ".gnupg"),
    os.path.join(_HOME, ".kube"), os.path.join(_HOME, ".docker"), os.path.join(_HOME, ".config"),
    os.path.join(_HOME, ".azure"), os.path.join(_HOME, ".gcloud"),
    "/etc", "/private/etc", "/root", "/proc", "/sys", "/dev", "/private/var/db", "/var/db",
]

_LIBRARY = os.path.join(_HOME, "Library")
_LIBRARY_ALLOWED = [os.path.join(_LIBRARY, "CloudStorage"), os.path.join(_LIBRARY, "Mobile Documents")]

_DENY_WRITE_DIRS = [
    "/usr", "/bin", "/sbin", "/System", "/Library", "/opt", "/var", "/private/var", "/boot", "/Applications",
    _PLUGIN_DIR,
]
_WRITE_ALLOWED_UNDER_DENIED = ["/var/folders", "/private/var/folders", "/var/tmp", "/private/var/tmp",
                               "/private/tmp", "/tmp"]  # nosec B108









_WINDOWS_SYSTEM = [os.environ.get(k, "") for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)",
                                                   "ProgramData")]


def _windows_credential_dirs() -> list[str]:
    roaming = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    system_root = os.environ.get("SYSTEMROOT", "")
    out = []
    if roaming:
        out += [os.path.join(roaming, "Microsoft", sub)
                for sub in ("Credentials", "Crypto", "Protect", "SystemCertificates", "Vault")]
        out += [os.path.join(roaming, "Mozilla", "Firefox", "Profiles"),
                os.path.join(roaming, "Thunderbird", "Profiles")]
    if local:
        out += [os.path.join(local, "Microsoft", sub) for sub in ("Credentials", "Vault")]
        out += [os.path.join(local, "Google", "Chrome", "User Data"),
                os.path.join(local, "Chromium", "User Data"),
                os.path.join(local, "Microsoft", "Edge", "User Data"),
                os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")]
    if system_root:
        out.append(os.path.join(system_root, "System32", "config"))
    return [os.path.normpath(d) for d in out]


def _windows_write_denied_dirs() -> list[str]:



    if not IS_WINDOWS:
        return []
    out = [os.path.join(_HOME, "AppData"), os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")]
    for documents in _windows_documents_folders():
        out += [os.path.join(documents, "WindowsPowerShell"), os.path.join(documents, "PowerShell")]
    return [os.path.normpath(d) for d in out if d]


def _windows_documents_folders() -> list[str]:







    out = [os.path.join(_HOME, "Documents")]
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            value, _kind = winreg.QueryValueEx(key, "Personal")
        if isinstance(value, str) and value.strip():
            out.append(os.path.expandvars(value.strip()))
    except (ImportError, OSError):
        pass
    return out


def _install_dirs() -> list[str]:






    import sys

    out = [sys.prefix, sys.base_prefix, sys.exec_prefix]
    for prefix in (sys.base_prefix, sys.prefix):
        parent = os.path.dirname(os.path.normpath(prefix))
        if os.path.basename(parent).lower() == "apps":
            out.append(os.path.dirname(parent))
    try:
        from qgis.core import QgsApplication

        prefix_path = QgsApplication.prefixPath()
        if isinstance(prefix_path, str):
            out.append(prefix_path)
    except Exception:  # nosec B110
        pass
    return list(dict.fromkeys(os.path.normpath(d) for d in out if d and len(os.path.normpath(d)) > 3))


_WINDOWS_CREDENTIAL_DIRS = _windows_credential_dirs()
_WINDOWS_WRITE_DENIED = _windows_write_denied_dirs()
_INSTALL_DIRS: list[str] = []






_AGENT_WORK_DIRS = list(dict.fromkeys(
    [AGENT_TMP_DIR, AGENT_EXPORT_DIR, AGENT_CACHE_DIR]
    + [os.path.join(AGENT_ROOT, name) for name in ("tmp", "exports", "cache")]))
_MOUNT_ROOTS = ["/Volumes", "/mnt", "/media", "/srv", "/data", "/home", "/Users",
                "/tmp", "/private/tmp",
                "/var/folders", "/private/var/folders", "/var/tmp", "/private/var/tmp"]  # nosec B108













_AGENT_DIR_NAME = os.path.basename(os.path.normpath(AGENT_ROOT)).replace("-", "_").lower()


def _is_agent_dir_name(part: str) -> bool:

    return bool(_AGENT_DIR_NAME) and part.replace("-", "_").lower() == _AGENT_DIR_NAME




_NON_HTTP_PORTS = frozenset({
    21, 22, 23, 25, 110, 135, 137, 138, 139, 143, 389, 445, 465, 587, 636, 993, 995, 1433, 1521, 3306, 3389,
    5432, 5900, 6379, 11211, 27017,
})
_SECRET_NAME_RE = re.compile(
    r"(?i)^(?:\.env(?:\..*)?|id_(?:rsa|dsa|ecdsa|ed25519)(?:\..*)?|known_hosts|authorized_keys|passwd|shadow"
    r"|\.pgpass|\.netrc|_netrc|\.git-credentials|qgis-auth\.db|.*\.(?:pem|key|p12|pfx|jks|kdbx|ppk|ovpn|gpg|asc"
    r"|tfstate|keychain|keychain-db)|credentials?(?:\..*)?|secrets?(?:\..*)?"
    r"|.*(?:credential|secret|password|token).*\.(?:json|ya?ml|txt|ini|cfg|conf|toml|env|xml|properties)"

    r"|login data(?: for account)?|web data|cookies|local state|key[34]\.db|logins\.json|cookies\.sqlite)$"
)

_allowed_roots: list[str] = []
_output_root = ""
_user_hosts: set[str] = set()





_vouched_hosts: set[str] = set()
_vouched_thread = None
_VOUCHED_MAX = 4096

_user_text_seen = True




def expand_path(path: str) -> str:

    try:
        return os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser(path or ""))))
    except Exception:
        return path


def _is_case_insensitive() -> bool:
    try:
        return os.path.exists(__file__.upper()) and os.path.exists(__file__.lower())
    except Exception:
        return IS_WINDOWS


_FOLD_CASE = IS_WINDOWS or _is_case_insensitive()


def _norm(path: str) -> str:
    text = os.path.normpath(path)
    return text.lower() if _FOLD_CASE else text


def _under(path: str, root: str) -> bool:
    if not root:
        return False
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def _qgis_profile_dir() -> str:


    if "qgis.core" not in sys.modules:
        return ""
    try:
        from qgis.core import QgsApplication

        return os.path.normpath(QgsApplication.qgisSettingsDirPath() or "")
    except Exception:
        return ""


def _state_dirs() -> list[str]:
    try:
        from .policy import state_dir

        return [state_dir()]
    except Exception:
        return []


@functools.lru_cache(maxsize=1)
def temp_roots() -> list[str]:



    roots = [tempfile.gettempdir()] + _AGENT_WORK_DIRS
    return list(dict.fromkeys(os.path.realpath(r) for r in roots if r))


def project_dir() -> str:



    if "qgis.core" not in sys.modules:
        return ""
    try:
        from qgis.core import QgsProject

        name = QgsProject.instance().fileName() or ""
    except Exception:
        return ""




    return os.path.dirname(os.path.abspath(name)) if name else ""




_workspace_override = [""]


def set_workspace(path: str) -> None:
    _workspace_override[0] = str(path or "")


def workspace_dir() -> str:








    if _workspace_override[0]:
        return _workspace_override[0]
    folder = project_dir()
    if folder:
        return folder
    try:
        os.makedirs(AGENT_EXPORT_DIR, exist_ok=True)
    except OSError:
        pass
    return AGENT_EXPORT_DIR


def anchor(path) -> str:

    text = os.fspath(path) if isinstance(path, os.PathLike) else str(path)
    if text.lower().startswith(("/vsi", "http://", "https://")):
        return text
    expanded = os.path.expanduser(text)
    drive, rest = os.path.splitdrive(expanded)
    if drive or os.path.isabs(expanded) or rest.startswith(("/", "\\")):
        return text
    return os.path.join(workspace_dir(), expanded) if expanded not in ("", ".") else workspace_dir()


def allow_attached_paths(paths) -> None:







    for path in paths or []:
        full = expand_path(str(path))
        if full not in _attached_files:
            _attached_files.append(full)
        folder = os.path.dirname(os.path.realpath(str(path)))
        if not folder or folder in _allowed_roots:
            continue
        reason = _read_denied(folder, _explicit_roots())
        if reason and "hidden" not in reason:


            continue
        _allowed_roots.append(folder)


def set_output_folder(path: str | None) -> None:

    global _output_root
    _output_root = os.path.realpath(expand_path(path)) if path else ""


def _explicit_roots() -> list[str]:














    out = list(temp_roots())
    for root in (project_dir(), tempfile.gettempdir()):
        if root:
            out.extend((os.path.normpath(root), os.path.realpath(root)))
    if _output_root:
        out.append(_output_root)
    out.extend(_allowed_roots)
    return list(dict.fromkeys(out))












_user_paths: list[str] = []
_attached_files: list[str] = []
_USER_PATHS_MAX = 256
_own_paths: set[str] = set()
_OWN_PATHS_MAX = 4096



_roots_override: list = [None]
_scope_off_override = [False]
_LAYER_ROOTS: dict = {"key": None, "at": 0.0, "roots": []}
_LAYER_ROOTS_TTL_S = 5.0


def read_scope_enabled() -> bool:

    if _scope_off_override[0]:
        return False
    try:
        from . import tuning

        return tuning.flag("files", "read_scope_enabled", True)
    except Exception:  # noqa: BLE001
        return True


def set_read_roots(roots) -> None:

    if roots is None:
        _scope_off_override[0] = True
        _roots_override[0] = None
        return
    _scope_off_override[0] = False
    _roots_override[0] = [str(r) for r in roots if r]


def note_own_paths(paths) -> None:

    for path in paths or ():
        try:
            text = os.fspath(path)
        except TypeError:
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        if len(_own_paths) >= _OWN_PATHS_MAX:
            _own_paths.clear()
        full = expand_path(text.split("|", 1)[0])
        _own_paths.update({_norm(full), _norm(os.path.realpath(full))})


def _layer_folders() -> list[str]:






    try:
        return _read_layer_folders()
    except Exception:  # noqa: BLE001
        return list(_LAYER_ROOTS["roots"])


def _read_layer_folders() -> list[str]:
    from qgis.core import QgsProject, QgsProviderRegistry
    from qgis.PyQt.QtCore import QCoreApplication, QThread

    app = QCoreApplication.instance()
    if app is not None and QThread.currentThread() is not app.thread():
        return list(_LAYER_ROOTS["roots"])
    project = QgsProject.instance()
    key = (id(project), project.fileName(), project.count())
    now = time.monotonic()
    if _LAYER_ROOTS["key"] == key and now - _LAYER_ROOTS["at"] < _LAYER_ROOTS_TTL_S:
        return list(_LAYER_ROOTS["roots"])
    registry = QgsProviderRegistry.instance()
    roots: list[str] = []
    for layer in project.mapLayers().values():
        try:
            path = str(registry.decodeUri(layer.providerType(), layer.source()).get("path") or "")
        except (AttributeError, RuntimeError, TypeError):
            continue
        path = local_part(path)
        if not path:
            continue
        full = expand_path(path)
        roots.append(full)


        own = _norm(full) in _own_paths or _norm(os.path.realpath(full)) in _own_paths
        if not own and not os.path.isdir(full):
            roots.append(os.path.dirname(full))
    roots = list(dict.fromkeys(roots))
    _LAYER_ROOTS.update(key=key, at=now, roots=roots)
    return list(roots)


def local_part(path: str) -> str:

    text = (path or "").strip()
    if text.lower().startswith("file://"):
        try:
            from qgis.PyQt.QtCore import QUrl

            text = QUrl(text).toLocalFile() or text
        except Exception:  # noqa: BLE001
            text = urllib.parse.unquote(text[7:])
    if text.lower().startswith("/vsi"):
        kind, inner = unwrap_vsi(text)
        if kind != "local":
            return ""
        text = inner.split("|", 1)[0]
    if "://" in text or not text:
        return ""
    return text


def read_roots() -> list[str]:

    if _roots_override[0] is not None:
        return list(_roots_override[0])

    roots = list(temp_roots()) + [project_dir(), tempfile.gettempdir(), _output_root]
    roots += _layer_folders() + [_PLUGIN_RESOURCES] + list(_attached_files)
    try:
        from .output_paths import exports_folder

        roots.append(exports_folder())
    except Exception:  # nosec B110
        pass
    profile = _qgis_profile_dir()
    if profile:
        roots.append(os.path.join(profile, "python", "plugins"))



    roots = [root for root in roots if root and not _too_wide(root)] + list(_user_paths)
    out: list[str] = []
    for root in roots:
        if not root:
            continue
        out.append(os.path.normpath(root))
        try:
            out.append(os.path.realpath(root))
        except OSError:
            pass
    out.extend(sorted(_own_paths))
    return list(dict.fromkeys(out))


def _too_wide(root: str) -> bool:

    try:
        full = os.path.normpath(os.path.abspath(root))
    except (TypeError, ValueError):
        return True
    if os.path.dirname(full) == full or refused_share(full):
        return True
    return any(_under(home, full) for home in dict.fromkeys((_HOME, os.path.realpath(_HOME))))


def _sidecar_of_attached(path: str) -> bool:

    folder, stem = _norm(os.path.dirname(path)), _norm(os.path.splitext(os.path.basename(path))[0])
    for attached in _attached_files:
        if (_norm(os.path.dirname(attached)) == folder
                and _norm(os.path.splitext(os.path.basename(attached))[0]) == stem):
            return True
    return False


def _scope_problem(expanded: str, real: str) -> str | None:

    if not read_scope_enabled():
        return None
    candidates = list(dict.fromkeys((expanded, real)))
    if any(_norm(c) in _own_paths for c in candidates) or any(_sidecar_of_attached(c) for c in candidates):
        return None
    roots = read_roots()
    if any(_under(c, root) for c in candidates for root in roots):
        return None
    return (f"{expanded} is outside the project, its layers' folders and the files the user gave; the agent "
            "does not read or list other folders of this computer. Ask the user to attach the file in the "
            "chat or to type its full path, and never search their folders for it.")


def _hidden_component(path: str, roots: list[str]) -> str | None:

    if any(_under(path, root) for root in roots):
        return None
    drive, rest = os.path.splitdrive(path)
    for part in rest.replace("\\", "/").split("/"):
        if part.startswith(".") and part not in (".", "..") and not _is_agent_dir_name(part):
            return part
    return None


def _read_denied(path: str, roots: list[str]) -> str | None:

    if _under(path, _PLUGIN_RESOURCES):
        return None
    name = os.path.basename(path)
    if _SECRET_NAME_RE.match(name):
        return f"'{name}' looks like a credential file; the agent never reads it."
    for root in _DENY_READ_DIRS + _WINDOWS_CREDENTIAL_DIRS:
        if _under(path, root):
            return f"{root} holds credentials or system state; the agent never reads it."
    profile = _qgis_profile_dir()





    in_plugins = bool(profile) and _under(path, os.path.join(profile, "python", "plugins"))
    if (_under(path, _LIBRARY) and not in_plugins
            and not any(_under(path, ok) for ok in _LIBRARY_ALLOWED)):
        return "~/Library holds keychains and app state; the agent never reads it."
    if (profile and _under(path, profile) and not in_plugins
            and not any(_under(path, root) for root in roots)):
        return "The QGIS profile folder holds the auth database and settings; the agent never touches it."
    for state in _state_dirs():
        if _under(path, state):
            return "The agent's own state folder is off limits to tools."




    if (any(_under(path, root) for root in (AGENT_HOME, AGENT_ROOT))
            and not any(_under(path, d) for d in _AGENT_WORK_DIRS)):
        return "The agent's own state folder is off limits to tools."
    hidden = _hidden_component(path, roots)
    if hidden:
        return f"'{hidden}' is a hidden folder or file; the agent stays out of hidden paths."
    return None


def _write_root_ok(path: str, roots: list[str]) -> bool:
    if any(_under(path, root) for root in roots):
        return True
    if STRICT_WRITE_ROOTS:
        return False
    if _under(path, _HOME):
        return True
    if IS_WINDOWS:
        return not any(_under(path, d) for d in _WINDOWS_SYSTEM if d)
    return any(_under(path, root) for root in _MOUNT_ROOTS)











_VSI_REMOTE = ("/vsicurl/", "/vsicurl_streaming/", "/vsis3/", "/vsis3_streaming/", "/vsigs/", "/vsigs_streaming/",
               "/vsiaz/", "/vsiaz_streaming/", "/vsiadls/", "/vsioss/", "/vsioss_streaming/", "/vsiswift/",
               "/vsiswift_streaming/", "/vsihdfs/", "/vsiwebhdfs/")
_VSI_ARCHIVE = ("/vsizip/", "/vsitar/", "/vsigzip/", "/vsi7z/", "/vsirar/", "/vsisparse/", "/vsicrypt/")
_VSI_MEMORY = ("/vsimem/",)
_VSI_REFUSED = ("/vsistdin/", "/vsistdout/", "/vsistdin?", "/vsistdout?")


_VSI_ANY_RE = re.compile(r"(?i)^/vsi[a-z0-9_]+[/?]")
_VSI_WRAPPERS = ("/vsipmtiles/",)


def unwrap_vsi(path: str) -> tuple[str, str]:







    text = (path or "").strip()
    for _ in range(8):
        lowered = text.lower()
        if lowered.startswith(_VSI_REFUSED):
            return "refused", text
        if lowered.startswith(_VSI_REMOTE):
            return "remote", text.split("/", 2)[-1]
        if lowered.startswith(("/vsicurl?", "/vsicurl_streaming?")):
            query = urllib.parse.parse_qs(text.split("?", 1)[1])
            return "remote", (query.get("url") or [""])[0]
        if lowered.startswith(_VSI_MEMORY):
            return "memory", text
        if lowered.startswith("/vsisubfile/"):

            remainder = text[len("/vsisubfile/"):]
            text = remainder.split(",", 1)[1] if "," in remainder else remainder
            continue
        matched = next((prefix for prefix in _VSI_ARCHIVE + _VSI_WRAPPERS if lowered.startswith(prefix)), None)
        if matched is None:
            return ("unknown" if _VSI_ANY_RE.match(text) else "local"), text
        text = text[len(matched):]
    return "local", text


def _is_remote_share(path: str) -> bool:


    text = path.strip()
    head = text[:2].replace("/", "\\")
    if head != "\\\\" or len(text) < 3:
        return False
    if text[:2] == "//" and not IS_WINDOWS:
        return False
    return text[2] not in "\\/"


def _in_own_share_folder(path: str) -> bool:













    if "qgis.core" not in sys.modules:
        return False
    from . import output_paths

    for kind in output_paths._QT_LOCATION:
        folder = output_paths.standard_folder(kind)
        if _is_remote_share(folder) and _under(path, folder):
            return True
    return False


def refused_share(path: str) -> bool:









    return isinstance(path, str) and _is_remote_share(path) and not _in_own_share_folder(path)



_MAX_PATH = 260
_long_paths_state: bool | None = None


def _long_paths_ok() -> bool:








    global _long_paths_state
    if _long_paths_state is None:
        if not IS_WINDOWS:
            _long_paths_state = True
        else:
            try:
                import ctypes

                _long_paths_state = bool(ctypes.windll.ntdll.RtlAreLongPathsEnabled())
            except Exception:  # nosec B110
                _long_paths_state = False
    return _long_paths_state


def fits_path(path: str, margin: int = 0) -> bool:






    return len(path) + margin < _MAX_PATH or _long_paths_ok()


def _windows_path_problem(path: str) -> str | None:





    drive, tail = ntpath.splitdrive(path)
    if drive and not tail.startswith(("/", "\\")):
        return "Use an absolute Windows path such as C:/data/file.gpkg, not a drive-relative path."
    for part in re.split(r"[\\/]", tail):
        if not part or part in (".", ".."):
            continue
        if any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            return "The Windows path contains a reserved character or an alternate data stream."
        if part != part.rstrip(" ."):
            return "Windows path components must not end with a space or a dot."
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if re.fullmatch(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]", stem):
            return "The Windows path names a reserved device; choose an ordinary file name."
    return None


def validate_read(path: str) -> str | None:







    return validate_path(path, scoped=True)


def validate_path(path: str, write: bool = False, overwrite: bool | None = None,
                   overwrite_remedy: str | None = None, scoped: bool = False) -> str | None:











    if not isinstance(path, str) or not path.strip():
        return "The path is empty."
    if "\x00" in path:
        return "The path contains a null byte."
    if path.startswith(("http://", "https://")):
        return None
    if path.lower().startswith("/vsi"):
        kind, inner = unwrap_vsi(path)
        if kind == "refused":
            return "The agent does not read from or write to the standard streams."
        if kind == "unknown":
            return "The agent does not open this GDAL virtual path; name the file or its URL directly."
        if kind in ("remote", "memory"):
            return None

        path = inner.split("|", 1)[0] or inner
    if refused_share(path):






        return ("A path on another machine (\\\\host\\share) is not written or read by the agent; "
                "use a local folder.")
    expanded = expand_path(path)


    if refused_share(expanded):
        return "The expanded path is a network share; use a local folder."
    if IS_WINDOWS:
        reason = _windows_path_problem(path) or _windows_path_problem(expanded)
        if reason:
            return reason
        if write:
            from .output_paths import posix_root_problem

            reason = posix_root_problem(path)
            if reason:
                return reason



        if write and len(expanded) >= _MAX_PATH and not _long_paths_ok():
            return (f"The path is {len(expanded)} characters and Windows stops this process at "
                    f"{_MAX_PATH}; choose a shorter folder or file name.")
    try:
        real = os.path.realpath(expanded)
    except Exception:
        real = expanded
    roots = _explicit_roots()
    for candidate in dict.fromkeys((expanded, real)):
        reason = _read_denied(candidate, roots)
        if reason:
            return reason
    if not write:
        return _scope_problem(expanded, real) if scoped else None
    for candidate in dict.fromkeys((expanded, real)):
        if not _INSTALL_DIRS:
            _INSTALL_DIRS.extend(_install_dirs())
        for root in _DENY_WRITE_DIRS + [d for d in _WINDOWS_SYSTEM if d] + _WINDOWS_WRITE_DENIED + _INSTALL_DIRS:





            allowed = [] if root == _PLUGIN_DIR else _WRITE_ALLOWED_UNDER_DENIED + roots
            if _under(candidate, root) and not any(_under(candidate, ok) for ok in allowed):




                return (f"{expanded} is under {root}, a system or application folder; write under the project, "
                        "home or temp folder.")
    if not _write_root_ok(real, roots):
        return ("Writes are limited to the project folder, the temp folder, attached files' folders and your "
                "home folder or mounted volumes.")
    if overwrite is False and os.path.isfile(real):
        remedy = overwrite_remedy or ("Pass overwrite=true only after the user agreed to replace it, "
                                       "or choose a new file name.")
        return f"{expanded} already exists. {remedy}"
    return None


def validate_code(code: str) -> str | None:

    from .code_guard import validate_code as _validate

    return _validate(code)


def build_safe_builtins() -> dict[str, Any]:

    from .code_guard import build_safe_builtins as _build

    return _build()


def safe_open(path: str, mode: str = "r", *args, **kwargs):
    writing = any(flag in mode for flag in "wax+")
    error = validate_path(path, write=writing, overwrite=False if writing else None)
    if error:
        raise PermissionError(error)


    if "b" not in mode and len(args) < 2:
        kwargs.setdefault("encoding", "utf-8")
    return open(expand_path(path), mode, *args, **kwargs)


def safe_read_text(path: str, encoding: str = "utf-8", max_chars: int = 200_000) -> str:
    error = validate_path(path)
    if error:
        raise PermissionError(error)
    with open(expand_path(path), encoding=encoding, errors="replace") as handle:
        return handle.read(max(0, min(int(max_chars), 200_000)))




_LOCAL_HOSTS = ("localhost", "localhost.localdomain", "ip6-localhost")
_ALLOWED_SCHEMES = ("http", "https")
_HOST_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://)?((?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-f:]+\]|[a-z0-9][a-z0-9.-]*\.[a-z]{2,})"
    r"|(?<=://)([a-z0-9][a-z0-9-]*)"
)


def remember_user_text(text: str) -> None:






    global _user_text_seen
    _user_text_seen = True
    for match in _HOST_RE.finditer(text or ""):
        _user_hosts.add((match.group(1) or match.group(2) or "").strip("[]").lower().rstrip("."))
    for path in typed_paths(text):
        if len(_user_paths) >= _USER_PATHS_MAX:
            del _user_paths[:2]
        for spelling in dict.fromkeys((path, os.path.realpath(path))):
            if spelling not in _user_paths:
                _user_paths.append(spelling)




_PATH_START_RE = re.compile(r"(?:(?<=[\s\"'(\[<`])|^)(file:///?|~[\\/]|[A-Za-z]:[\\/]|/(?=[^\s/]))")
_PATH_END_CHARS = ".,;:!?)]}>\"'`"


def typed_paths(text: str) -> list[str]:






    found: list[str] = []
    for match in _PATH_START_RE.finditer(text or ""):
        start = match.start(1)
        rest = text[start:].split("\n", 1)[0]
        opener = text[start - 1] if start else ""
        closer = {'"': '"', "'": "'", "`": "`", "<": ">", "(": ")", "[": "]"}.get(opener)
        if closer and closer in rest:

            rest = rest[:rest.index(closer)]
        words = rest.split(" ")
        chosen = ""
        for count in range(min(len(words), 12), 0, -1):
            candidate = " ".join(words[:count]).rstrip(_PATH_END_CHARS)
            local = local_part(candidate) if candidate.lower().startswith("file:") else candidate
            if local and os.path.exists(expand_path(local)):
                chosen = local
                break
        if not chosen:
            first = words[0].rstrip(_PATH_END_CHARS)
            chosen = local_part(first) if first.lower().startswith("file:") else first
        if chosen and not _is_remote_share(chosen):
            full = expand_path(chosen)
            if full not in found:
                found.append(full)
    return found


_TEMPLATE_LABEL_RE = re.compile(r"^(?:\{[^}]*\}\.)+")


def normal_host(url: str) -> str:





    text = str(url or "").strip()
    match = re.match(r"(?i)^[a-z][a-z0-9+.-]*://([^/?#]*)", text)
    if match and "{" in match.group(1):
        text = text[:match.start(1)] + _TEMPLATE_LABEL_RE.sub("", match.group(1)) + text[match.end(1):]
    try:
        return _host_of(text)
    except ValueError:
        return ""


def _text_leaves(value, depth: int = 0):
    if isinstance(value, str):
        yield value
    elif depth < 12 and isinstance(value, dict):
        for item in value.values():
            yield from _text_leaves(item, depth + 1)
    elif depth < 12 and isinstance(value, (list, tuple)):
        for item in value:
            yield from _text_leaves(item, depth + 1)


def vouch_for_urls(values) -> None:

    for value in _text_leaves(values):
        for match in _URL_IN_TEXT_RE.finditer(value[:20_000]):
            host = normal_host(match.group(0))
            if host:
                if len(_vouched_hosts) >= _VOUCHED_MAX:
                    _vouched_hosts.clear()
                _vouched_hosts.add(host)


def host_is_vouched(host: str) -> bool:

    host = str(host or "").strip().lower().rstrip(".")
    return bool(host) and (host in _user_hosts or host in _vouched_hosts)


def forget_vouched_hosts() -> None:

    global _vouched_thread
    _vouched_hosts.clear()
    _vouched_thread = None


def vouched_for_thread(thread_id) -> None:







    global _vouched_thread
    if not thread_id or thread_id != _vouched_thread:
        _vouched_hosts.clear()
    _vouched_thread = thread_id or None


_URL_IN_TEXT_RE = re.compile(r"(?i)https?://[^\s\"'<>|\\]+")





































_DNS_TTL_S = 30.0
_DNS_TIMEOUT_S = 2.0
_DNS_CACHE_MAX = 256
_dns_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
_dns_lock = threading.Lock()


def _getaddrinfo(host: str) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError, ValueError):
        return ()
    return tuple(sorted({info[4][0].split("%")[0] for info in infos if info[4]}))


def resolve_host(host: str) -> tuple[str, ...]:










    now = time.monotonic()
    with _dns_lock:
        entry = _dns_cache.get(host)
        if entry is not None and entry[0] > now:
            return entry[1]
    answer: list[tuple[str, ...]] = []
    worker = threading.Thread(target=lambda: answer.append(_getaddrinfo(host)),
                              name="ai-agent-dns", daemon=True)
    worker.start()
    worker.join(_DNS_TIMEOUT_S)
    if not answer:


        return ()
    addresses = answer[0]
    with _dns_lock:
        if len(_dns_cache) >= _DNS_CACHE_MAX:
            _dns_cache.clear()
        _dns_cache[host] = (now + _DNS_TTL_S, addresses)
    return addresses


def forget_resolved_hosts() -> None:

    with _dns_lock:
        _dns_cache.clear()


_LEGACY_IPV4_PART_RE = re.compile(r"^(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)$")


def _legacy_ipv4(text: str):






    parts = text.split(".")
    if not 1 <= len(parts) <= 4 or not all(_LEGACY_IPV4_PART_RE.match(p) for p in parts):
        return None
    values = [int(p, 16) if p[:2].lower() == "0x" else int(p, 8) if p.startswith("0") else int(p)
              for p in parts]
    tail_bits = 8 * (5 - len(parts))
    if any(v > 255 for v in values[:-1]) or values[-1] >= 1 << tail_bits:
        return None
    packed = 0
    for v in values[:-1]:
        packed = (packed << 8) | v
    packed = (packed << tail_bits) | values[-1]
    return ipaddress.IPv4Address(packed)


_NAT64_PREFIXES = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


_METADATA_ADDRESSES = frozenset(ipaddress.ip_address(a) for a in (
    "168.63.129.16", "100.100.100.200", "fd00:ec2::254",
))
_METADATA_NAMES = ("metadata.google.internal", "metadata.goog", "instance-data.ec2.internal")


def _host_of(url: str) -> str:






    host = (urllib.parse.urlsplit(url).hostname or "").strip("[]").lower()
    return host.split("%", 1)[0].rstrip(".")


def _as_address(text: str):







    text = (text or "").split("%", 1)[0].rstrip(".")
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return _legacy_ipv4(text)
    if address.version != 6 or address in _METADATA_ADDRESSES:
        return address
    for inner in (address.ipv4_mapped, address.sixtofour, (address.teredo or (None, None))[1]):
        if inner is not None:
            return inner
    if any(address in net for net in _NAT64_PREFIXES) or (
            address in _IPV4_COMPATIBLE and int(address) >> 24):
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address


def _address_is_local(address) -> bool:

    return bool(address.is_loopback or address.is_link_local or address.is_unspecified
                or address.is_multicast or address.is_reserved or address in _METADATA_ADDRESSES)


def _address_is_private(address) -> bool:

    if _address_is_local(address):
        return False
    return bool(address.is_private or (address.version == 4 and address in _CGNAT))


def is_local_url(url: str, resolve: bool = True) -> bool:






    try:
        host = _host_of(url)
    except ValueError:
        return True
    if not host:
        return True
    if host in _LOCAL_HOSTS or host.endswith(".localhost") or host in _METADATA_NAMES:
        return True
    literal = _as_address(host)
    if literal is not None:
        return _address_is_local(literal)
    if not resolve:
        return False
    for text in resolve_host(host):
        address = _as_address(text)
        if address is not None and _address_is_local(address):
            return True
    return False


def is_private_url(url: str, resolve: bool = True) -> bool:

    try:
        host = _host_of(url)
    except ValueError:
        return False
    if not host:
        return False
    literal = _as_address(host)
    if literal is not None:
        return _address_is_private(literal)
    if "." not in host:
        return True
    if not resolve:
        return False


    for text in resolve_host(host):
        address = _as_address(text)
        if address is not None and _address_is_private(address):
            return True
    return False


def vetted_addresses(url: str):





















    try:
        host = _host_of(url)
    except ValueError:
        return None
    if not host or _as_address(host) is not None:
        return ()
    addresses = resolve_host(host)
    for text in addresses:
        address = _as_address(text)
        if address is not None and _address_is_local(address):
            return None
    return addresses


def refused_addresses(url: str, addresses) -> str | None:





    host = _host_of(url)
    for text in addresses:
        address = _as_address(text)
        if address is None:
            continue
        if _address_is_local(address) and not is_paired_backend_url(url):
            return f"{host} resolved to a local address at connection time."
        if (_address_is_private(address) and _user_text_seen and host not in _user_hosts
                and not is_paired_backend_url(url)):
            return f"{host} resolved to a private network address the user did not name."
    return None


def is_paired_backend_url(url: str) -> bool:





    try:
        from .settings import Settings

        parts = urllib.parse.urlsplit(str(url or ""))
        server = urllib.parse.urlsplit(str(Settings().server_url or ""))
    except Exception:  # noqa: BLE001
        return False
    try:
        host = (parts.hostname or "").strip("[]").lower().rstrip(".")
        theirs = (server.hostname or "").strip("[]").lower().rstrip(".")
        return bool(host) and host == theirs and parts.port == server.port
    except ValueError:
        return False


def validate_url(url: str) -> str | None:

    text = str(url or "").strip()
    try:
        scheme = urllib.parse.urlsplit(text).scheme.lower()
    except ValueError:
        return "The URL cannot be parsed."
    if scheme not in _ALLOWED_SCHEMES:
        return f"Only http and https URLs are fetched, not {scheme or 'a bare path'}://."
    try:
        port = urllib.parse.urlsplit(text).port
    except ValueError:
        return "The URL cannot be parsed."
    if port in _NON_HTTP_PORTS:
        return f"Port {port} answers a protocol other than HTTP and is not fetched."






    if is_local_url(text) and not is_paired_backend_url(text):
        return "URLs on this machine or on link-local addresses are not fetched."
    if is_private_url(text) and _user_text_seen and not is_paired_backend_url(text):
        host = _host_of(text)
        if host not in _user_hosts:
            return (f"{host} is a private network address the user did not name; ask the user for the "
                    "address before fetching it.")
    return None


def qgis_proxy_address() -> str | None:











    try:
        from qgis.core import QgsSettings

        from .proxy_credentials import qgis_proxy_credentials

        settings = QgsSettings()
        if not settings.value("proxy/proxyEnabled", False, type=bool):
            return None
        kind = str(settings.value("proxy/proxyType", "DefaultProxy", type=str) or "")
        host = str(settings.value("proxy/proxyHost", "", type=str) or "").strip()
        port = str(settings.value("proxy/proxyPort", "", type=str) or "").strip()





        user, password = qgis_proxy_credentials()
        excluded = settings.value("proxy/proxyExcludedUrls", "", type=str) or ""
    except Exception:  # noqa: BLE001
        return None
    if kind not in ("HttpProxy", "HttpCachingProxy") or not host:
        return None




    if excluded and not os.environ.get("no_proxy"):
        os.environ["no_proxy"] = ",".join(x.strip() for x in str(excluded).split("|") if x.strip())
    auth = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(password, safe='')}@" if user else ""
    return f"http://{auth}{host}{':' + port if port else ''}"


def _system_proxy_address() -> str | None:



















    try:
        from .ws_client import resolve_proxy

        found = resolve_proxy("example.com", 443, True)
    except Exception:  # noqa: BLE001
        return None
    if not found or not found.get("host"):
        return None
    user = str(found.get("user") or "")
    password = str(found.get("password") or "")
    auth = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(password, safe='')}@" if user else ""
    port = found.get("port") or 0
    return f"http://{auth}{found['host']}{':' + str(port) if port else ''}"


def apply_qgis_proxy() -> bool:

















    from . import net

    net.set_proxy(qgis_proxy_address() or _system_proxy_address())
    _apply_qgis_trust(net)
    return net.proxy_in_use() is not None


def _apply_qgis_trust(net) -> None:

    try:
        from .logger import log
        from .ws_client import resolve_tls




        ca_pem, _ = resolve_tls("")
        if net.set_trust(ca_pem):
            log("Tool fetches now also trust the certificates QGIS trusts.")
    except Exception as exc:  # noqa: BLE001
        try:
            from .logger import log_warning as _warn

            _warn(f"QGIS trusted certificates not read: {exc}")
        except Exception:  # nosec B110
            pass
