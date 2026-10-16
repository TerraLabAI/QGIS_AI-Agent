# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



























from __future__ import annotations

import contextlib
import functools
import ntpath
import os
import re
import sys
import tempfile
import time
import urllib.parse

from .host_platform import IS_WINDOWS
from .net_proxy import apply_qgis_proxy  # noqa: F401
from .policy import AGENT_CACHE_DIR, AGENT_EXPORT_DIR, AGENT_HOME, AGENT_ROOT, AGENT_TMP_DIR
from .security_urls import (  # noqa: F401
    PUBLIC_SECTOR_LABELS,
    PUBLIC_SECTOR_SUFFIXES,
    forget_resolved_hosts,
    host_is_vouched,
    is_local_url,
    is_paired_backend_url,
    is_private_url,
    local_url_refusal,
    look_up_ahead,
    name_to_resolve,
    normal_host,
    public_sector_host,
    refused_addresses,
    remember_user_hosts,
    resolve_host,
    start_lookup,
    validate_url,
    vetted_addresses,
    vouch_for_urls,
    vouched_hosts_for,
)
from .serialization import CodedText




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


def _in_agent_spelling(path: str) -> str:


    root = os.path.normpath(AGENT_ROOT)
    parent, real_name = os.path.dirname(root), os.path.basename(root)
    if not _under(path, parent) or _norm(path) == _norm(parent):
        return ""
    first, _, rest = path[len(parent.rstrip(os.sep)) + 1:].partition(os.sep)
    if first == real_name or not _is_agent_dir_name(first):
        return ""
    return os.path.join(parent, real_name, rest)


_SECRET_NAME_RE = re.compile(
    r"(?i)^(?:\.env(?:\..*)?|id_(?:rsa|dsa|ecdsa|ed25519)(?:\..*)?|known_hosts|authorized_keys|passwd|shadow"
    r"|\.pgpass|\.netrc|_netrc|\.git-credentials|qgis-auth\.db|.*\.(?:pem|key|p12|pfx|jks|kdbx|ppk|ovpn|gpg|asc"
    r"|tfstate|keychain|keychain-db)|credentials?(?:\..*)?|secrets?(?:\..*)?"
    r"|.*(?:credential|secret|password|token).*\.(?:json|ya?ml|txt|ini|cfg|conf|toml|env|xml|properties)"

    r"|login data(?: for account)?|web data|cookies|local state|key[34]\.db|logins\.json|cookies\.sqlite)$"
)

_allowed_roots: list[str] = []
_output_root = ""




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


def _svg_folders() -> list[str]:




    if "qgis.core" not in sys.modules:
        return []
    try:
        from qgis.core import QgsApplication

        folders = [os.path.normpath(p) for p in QgsApplication.svgPaths() if p]
    except Exception:
        return []
    profile = _qgis_profile_dir()
    return [f for f in folders if not _too_wide(f) and not (profile and _under(profile, f))]


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








_PROJECT_FILE = [""]


def project_dir() -> str:



    if "qgis.core" not in sys.modules:
        return ""
    from .background import on_main_thread

    if on_main_thread():
        try:
            from qgis.core import QgsProject

            _PROJECT_FILE[0] = QgsProject.instance().fileName() or ""
        except Exception:
            return ""
    name = _PROJECT_FILE[0]




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


def attached_file_named(name: str) -> str:






    text = str(name or "").strip()
    if not text or "/" in text or "\\" in text:
        return ""
    wanted = _norm(text)
    for path in reversed(_attached_files):
        with contextlib.suppress(Exception):
            if _norm(os.path.basename(path)) == wanted and os.path.isfile(path):
                return path
    return ""


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
_FOLDERS: dict[str, bool] = {}
_FOLDERS_MAX = 8192




_thread_roots: list[str] = []
_THREAD_ROOTS_MAX = 512
_thread_roots_keep: list = [None]


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


def write_roots() -> dict:

    return {"project_dir": project_dir(), "output_root": _output_root, "allowed": list(_allowed_roots),
            "shares": _shares_in_use()}


def set_write_roots(roots) -> None:





    if not isinstance(roots, dict):
        return
    _shares_override[0] = [str(root) for root in roots.get("shares") or [] if root]
    set_output_folder(roots.get("output_root") or None)
    for root in list(roots.get("allowed") or []) + [roots.get("project_dir") or ""]:
        if not root:
            continue


        for folder in dict.fromkeys((os.path.abspath(str(root)), os.path.realpath(str(root)))):
            if folder not in _allowed_roots:
                _allowed_roots.append(folder)


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
        current = _read_layer_folders()
    except Exception:  # noqa: BLE001
        current = list(_LAYER_ROOTS["roots"])
    return list(dict.fromkeys(current + _thread_roots))


def _keep_thread_roots(roots: list[str]) -> None:

    known = set(_thread_roots)
    new = [root for root in roots if root not in known]
    if not new:
        return
    _thread_roots.extend(new)
    del _thread_roots[:-_THREAD_ROOTS_MAX]
    keep = _thread_roots_keep[0]
    if keep is None:
        return
    try:
        keep(list(_thread_roots))
    except Exception as exc:  # noqa: BLE001
        from .logger import log_warning

        log_warning(f"read scope: the conversation's layer folders were not stored: {exc}")


def _read_layer_folders() -> list[str]:
    from qgis.core import QgsProject, QgsProviderRegistry

    from .background import on_main_thread

    if not on_main_thread():
        return list(_LAYER_ROOTS["roots"])
    project = QgsProject.instance()
    key = (id(project), project.fileName(), project.count())
    now = time.monotonic()
    if _LAYER_ROOTS["key"] == key and now - _LAYER_ROOTS["at"] < _LAYER_ROOTS_TTL_S:
        return list(_LAYER_ROOTS["roots"])
    registry = QgsProviderRegistry.instance()
    roots: list[str] = []



    own_names = {os.path.basename(own) for own in _own_paths}
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


        norm = _norm(full)
        own = norm in _own_paths or (os.path.basename(norm) in own_names
                                     and _norm(os.path.realpath(full)) in _own_paths)
        if not own and not _is_folder(full):
            roots.append(os.path.dirname(full))
    roots = list(dict.fromkeys(roots))
    _LAYER_ROOTS.update(key=key, at=now, roots=roots)
    _keep_thread_roots(roots)
    return list(roots)


def _is_folder(path: str) -> bool:


    known = _FOLDERS.get(path)
    if known is None:
        if len(_FOLDERS) >= _FOLDERS_MAX:
            _FOLDERS.clear()
        known = _FOLDERS[path] = os.path.isdir(path)
    return known


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

        roots.append(exports_folder(make=False))
    except Exception:  # nosec B110
        pass
    profile = _qgis_profile_dir()
    if profile:
        roots.append(os.path.join(profile, "python", "plugins"))
    roots += _svg_folders()



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


    if any(_under(real, root) for root in roots):
        return None
    if os.path.lexists(expanded) and any(
            _under(_in_agent_spelling(c), work) for c in candidates for work in _AGENT_WORK_DIRS):


        return None
    if not os.path.lexists(expanded) and not any(ch in os.path.basename(expanded) for ch in "*?["):



        drive, rest = os.path.splitdrive(expanded)
        parts = rest.replace("\\", "/").split("/")
        typo = next((part for part in parts if _is_agent_dir_name(part)
                     and part != os.path.basename(os.path.normpath(AGENT_ROOT))), "")
        if typo:
            return CodedText(f"No such file or folder: {expanded}.", "agent_folder_misspelled", path=expanded,
                             agent_folder=os.path.basename(os.path.normpath(AGENT_ROOT)), typo=typo)
        return f"No such file or folder: {expanded}."
    return CodedText(f"{expanded} is outside the project, its layers' folders and the files the user gave; the "
                     "agent does not read or list other folders of this computer.",
                     "read_outside_scope", path=expanded)


def _hidden_component(path: str, roots: list[str]) -> str | None:

    if any(_under(path, root) for root in roots):
        return None
    drive, rest = os.path.splitdrive(path)
    for part in rest.replace("\\", "/").split("/"):
        if part.startswith(".") and part not in (".", "..") and not _is_agent_dir_name(part):
            return part
    return None


def _is_style_undo_file(path: str) -> bool:








    folder, name = os.path.split(path)
    if not name.lower().endswith(".qml") or os.path.basename(folder) != "style_undo":
        return False
    accounts = os.path.dirname(os.path.dirname(folder))
    return any(_norm(accounts) == _norm(os.path.join(state, "accounts")) for state in _state_dirs())


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






    in_plugins = (bool(profile) and _under(path, os.path.join(profile, "python", "plugins"))
                  or any(_under(path, folder) for folder in _svg_folders()))
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









    return (isinstance(path, str) and _is_remote_share(path) and not _in_own_share_folder(path)
            and not _in_used_share(path))




_shares_override: list = [None]


def _shares_in_use() -> list[str]:

    if _shares_override[0] is not None:
        return list(_shares_override[0])
    if "qgis.core" not in sys.modules:
        return []
    return [root for root in [project_dir()] + _layer_folders() if root and _is_remote_share(root)]


def _in_used_share(path: str) -> bool:









    return any(_under(path, root) for root in _shares_in_use())



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
        return CodedText(f"{path} is a drive-relative Windows path.", "windows_path_drive_relative", path=path)
    for part in re.split(r"[\\/]", tail):
        if not part or part in (".", ".."):
            continue
        if any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            return "The Windows path contains a reserved character or an alternate data stream."
        if part != part.rstrip(" ."):
            return "Windows path components must not end with a space or a dot."
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if re.fullmatch(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]", stem):
            return CodedText("The Windows path names a reserved device.", "windows_reserved_device", name=part)
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
            return CodedText("The agent does not open this GDAL virtual path.", "gdal_virtual_path_refused", path=path)
        if kind in ("remote", "memory"):
            return None

        path = inner.split("|", 1)[0] or inner
    if refused_share(path):







        return CodedText("A path on another machine (\\\\host\\share) is not written or read by the agent.",
                         "network_share_refused")
    expanded = expand_path(path)


    if refused_share(expanded):
        return CodedText("The expanded path is a network share.", "network_share_refused")
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
            return CodedText(f"The path is {len(expanded)} characters and Windows stops this process at "
                             f"{_MAX_PATH}.", "windows_path_too_long", length=len(expanded), limit=_MAX_PATH)
    try:
        real = os.path.realpath(expanded)
    except Exception:
        real = expanded
    if not write and all(_is_style_undo_file(c) for c in (expanded, real)):
        return None
    roots = _explicit_roots()
    for candidate in dict.fromkeys((expanded, real)):
        reason = _read_denied(candidate, roots)
        if reason:
            return reason
    if not write:
        return _scope_problem(expanded, real) if scoped else None



    profile = _qgis_profile_dir()
    plugins = [os.path.join(profile, "python", "plugins")] if profile else []
    for candidate in dict.fromkeys((expanded, real)):
        if not _INSTALL_DIRS:
            _INSTALL_DIRS.extend(_install_dirs())
        for root in (_DENY_WRITE_DIRS + [d for d in _WINDOWS_SYSTEM if d] + _WINDOWS_WRITE_DENIED + _INSTALL_DIRS
                     + plugins):





            allowed = [] if root == _PLUGIN_DIR or root in plugins else _WRITE_ALLOWED_UNDER_DENIED + roots
            if _under(candidate, root) and not any(_under(candidate, ok) for ok in allowed):




                return CodedText(f"{expanded} is under {root}, a system or application folder.",
                                 "write_system_folder", path=expanded, root=root)
    if not _write_root_ok(real, roots):
        return CodedText(f"{expanded} is outside the folders the agent writes to.", "write_scope", path=expanded)
    if overwrite is False and os.path.isfile(real):
        if not overwrite_remedy:
            return CodedText(f"{expanded} already exists.", "file_exists", path=expanded)
        return f"{expanded} already exists. {overwrite_remedy}"
    return None


def safe_open(path: str, mode: str = "r", *args, **kwargs):
    writing = any(flag in mode for flag in "wax+")
    error = validate_path(path, write=writing, overwrite=False if writing else None)
    if error:
        raise PermissionError(error)


    if "b" not in mode and len(args) < 2:
        kwargs.setdefault("encoding", "utf-8")
    return open(expand_path(path), mode, *args, **kwargs)


def safe_read_text(path: str, encoding: str = "utf-8", max_chars: int = 200_000) -> str:
    error = validate_read(path)
    if error:
        raise PermissionError(error)
    with open(expand_path(path), encoding=encoding, errors="replace") as handle:
        return handle.read(max(0, min(int(max_chars), 200_000)))




def remember_user_text(text: str) -> None:






    remember_user_hosts(text)
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


def vouched_for_thread(thread_id, layer_roots=(), keep_layer_roots=None) -> None:










    if vouched_hosts_for(thread_id):
        _thread_roots.clear()
        _thread_roots.extend(str(root) for root in list(layer_roots or ())[-_THREAD_ROOTS_MAX:]
                             if isinstance(root, str) and root)
        _LAYER_ROOTS["key"] = None
    _thread_roots_keep[0] = keep_layer_roots if thread_id else None
    if thread_id:
        _layer_folders()

