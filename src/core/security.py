# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Filesystem and network guards the tool modules import."""
















from __future__ import annotations

import ipaddress
import ntpath
import os
import re
import socket
import tempfile
import threading
import time
import urllib.parse
from typing import Any

from .policy import AGENT_HOME
from .provider_uri import encode_uri_url  # noqa: F401  re-exported for the tools


STRICT_WRITE_ROOTS = False

_WINDOWS = os.name == "nt"
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
                               "/private/tmp", "/tmp"]  # nosec B108 - allowlist of temp roots, not a temp file









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


_WINDOWS_CREDENTIAL_DIRS = _windows_credential_dirs()
_MOUNT_ROOTS = ["/Volumes", "/mnt", "/media", "/srv", "/data", "/home", "/Users", "/tmp", "/private/tmp",
                "/var/folders", "/private/var/folders", "/var/tmp", "/private/var/tmp"]  # nosec B108





_WINDOWS_UNC_RE = re.compile(r"^\\\\[^\\/]")


_NON_HTTP_PORTS = frozenset({
    21, 22, 23, 25, 110, 135, 137, 138, 139, 143, 389, 445, 465, 587, 636, 993, 995, 1433, 1521, 3306, 3389,
    5432, 5900, 6379, 11211, 27017,
})
_SECRET_NAME_RE = re.compile(
    r"(?i)^(?:\.env(?:\..*)?|id_(?:rsa|dsa|ecdsa|ed25519)(?:\..*)?|known_hosts|authorized_keys|passwd|shadow"
    r"|\.pgpass|\.netrc|_netrc|\.git-credentials|qgis-auth\.db|.*\.(?:pem|key|p12|pfx|jks|kdbx|ppk|ovpn|gpg|asc"
    r"|tfstate|keychain|keychain-db)|credentials?(?:\..*)?|secrets?(?:\..*)?"
    r"|.*(?:credential|secret|password|token).*\.(?:json|ya?ml|txt|ini|cfg|conf|toml|env|xml|properties))$"
)

_allowed_roots: list[str] = []
_output_root = ""
_user_hosts: set[str] = set()

_user_text_seen = True




def expand_path(path: str) -> str:
    """User and env expansion plus absolute normalisation. Never raises."""
    try:
        return os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser(path or ""))))
    except Exception:
        return path


def _is_case_insensitive() -> bool:
    try:
        return os.path.exists(__file__.upper()) and os.path.exists(__file__.lower())
    except Exception:
        return _WINDOWS


_FOLD_CASE = _WINDOWS or _is_case_insensitive()


def _norm(path: str) -> str:
    text = os.path.normpath(path)
    return text.lower() if _FOLD_CASE else text


def _under(path: str, root: str) -> bool:
    if not root:
        return False
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def _qgis_profile_dir() -> str:
    try:
        from qgis.core import QgsApplication

        return os.path.normpath(QgsApplication.qgisSettingsDirPath() or "")
    except Exception:
        return ""


def _state_dirs() -> list[str]:
    try:
        from .settings import state_dir

        return [state_dir()]
    except Exception:
        return []


def temp_roots() -> list[str]:
    roots = [tempfile.gettempdir(), AGENT_HOME]
    return list(dict.fromkeys(os.path.realpath(r) for r in roots if r))


def project_dir() -> str:
    try:
        from qgis.core import QgsProject

        name = QgsProject.instance().fileName() or ""
    except Exception:
        return ""
    return os.path.dirname(os.path.realpath(name)) if name else ""


def allow_attached_paths(paths) -> None:
    """The folders of files the user attached become write roots (called by the controller)."""
    for path in paths or []:
        folder = os.path.dirname(os.path.realpath(str(path)))
        if not folder or folder in _allowed_roots:
            continue
        reason = _read_denied(folder, _explicit_roots())
        if reason and "hidden" not in reason:


            continue
        _allowed_roots.append(folder)


def set_output_folder(path: str | None) -> None:
    """The output folder the user chose in the settings, when there is one."""
    global _output_root
    _output_root = os.path.realpath(expand_path(path)) if path else ""


def _explicit_roots() -> list[str]:
    """Allowed roots in both spellings, real and as given (macOS temp dirs live behind /private)."""
    out = []
    for root in temp_roots() + [project_dir(), tempfile.gettempdir(), _output_root] + list(_allowed_roots):
        if root:
            out.extend((os.path.normpath(root), os.path.realpath(root)))
    return list(dict.fromkeys(out))


def _hidden_component(path: str, roots: list[str]) -> str | None:
    """The first dot-folder or dot-file on the path outside an allowed root, else None."""
    if any(_under(path, root) for root in roots):
        return None
    drive, rest = os.path.splitdrive(path)
    for part in rest.replace("\\", "/").split("/"):
        if part.startswith(".") and part not in (".", ".."):
            return part
    return None


def _read_denied(path: str, roots: list[str]) -> str | None:
    """Why ``path`` may not be read at all, else None."""
    if _under(path, _PLUGIN_RESOURCES):
        return None
    name = os.path.basename(path)
    if _SECRET_NAME_RE.match(name):
        return f"'{name}' looks like a credential file; the agent never reads it."
    for root in _DENY_READ_DIRS + _WINDOWS_CREDENTIAL_DIRS:
        if _under(path, root):
            return f"{root} holds credentials or system state; the agent never reads it."
    if _under(path, _LIBRARY) and not any(_under(path, ok) for ok in _LIBRARY_ALLOWED):
        return "~/Library holds keychains and app state; the agent never reads it."
    profile = _qgis_profile_dir()
    if profile and _under(path, profile) and not any(_under(path, root) for root in roots):
        return "The QGIS profile folder holds the auth database and settings; the agent never touches it."
    for state in _state_dirs():
        if _under(path, state):
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
    if _WINDOWS:
        return not any(_under(path, d) for d in _WINDOWS_SYSTEM if d)
    return any(_under(path, root) for root in _MOUNT_ROOTS)











_VSI_REMOTE = ("/vsicurl/", "/vsicurl_streaming/", "/vsis3/", "/vsis3_streaming/", "/vsigs/", "/vsigs_streaming/",
               "/vsiaz/", "/vsiaz_streaming/", "/vsiadls/", "/vsioss/", "/vsioss_streaming/", "/vsiswift/",
               "/vsiswift_streaming/", "/vsihdfs/", "/vsiwebhdfs/")
_VSI_ARCHIVE = ("/vsizip/", "/vsitar/", "/vsigzip/", "/vsi7z/", "/vsirar/", "/vsisparse/", "/vsicrypt/")
_VSI_MEMORY = ("/vsimem/",)
_VSI_REFUSED = ("/vsistdin/", "/vsistdout/")


def unwrap_vsi(path: str) -> tuple[str, str]:
    """Split a GDAL virtual path into (kind, rest)."""






    text = (path or "").strip()
    for _ in range(8):
        lowered = text.lower()
        if lowered.startswith(_VSI_REFUSED):
            return "refused", text
        if lowered.startswith(_VSI_REMOTE):
            return "remote", text.split("/", 2)[-1]
        if lowered.startswith(_VSI_MEMORY):
            return "memory", text
        if lowered.startswith("/vsisubfile/"):

            remainder = text[len("/vsisubfile/"):]
            text = remainder.split(",", 1)[1] if "," in remainder else remainder
            continue
        matched = next((prefix for prefix in _VSI_ARCHIVE if lowered.startswith(prefix)), None)
        if matched is None:
            return "local", text
        text = text[len(matched):]
    return "local", text


def _is_remote_share(path: str) -> bool:
    """True for a UNC path in any of its spellings: \\\\host, //host on Windows, the mixed /\\host and \\/host forms, and the \\\\?\\UNC\\ and \\\\.\\."""

    text = path.strip()
    head = text[:2].replace("/", "\\")
    if head != "\\\\" or len(text) < 3:
        return False
    if text[:2] == "//" and not _WINDOWS:
        return False
    return text[2] not in "\\/"



_MAX_PATH = 260
_long_paths_state: bool | None = None


def _long_paths_ok() -> bool:
    """True when this process can open a path longer than 260 characters."""







    global _long_paths_state
    if _long_paths_state is None:
        if not _WINDOWS:
            _long_paths_state = True
        else:
            try:
                import ctypes

                _long_paths_state = bool(ctypes.windll.ntdll.RtlAreLongPathsEnabled())
            except Exception:  # nosec B110 - an unreadable flag means assume the limit
                _long_paths_state = False
    return _long_paths_state


def _windows_path_problem(path: str) -> str | None:
    """Reject Win32 aliases and device names before any file API sees them."""




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


def validate_path(path: str, write: bool = False, overwrite: bool | None = None) -> str | None:
    """None when the path may be used, else one sentence saying why not."""





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
        if kind in ("remote", "memory"):
            return None

        path = inner.split("|", 1)[0] or inner
    if _is_remote_share(path):





        return ("A path on another machine (\\\\host\\share) is not written or read by the agent; "
                "use a local folder.")
    expanded = expand_path(path)


    if _is_remote_share(expanded):
        return "The expanded path is a network share; use a local folder."
    if _WINDOWS:
        reason = _windows_path_problem(path) or _windows_path_problem(expanded)
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
        return None
    for candidate in dict.fromkeys((expanded, real)):
        for root in _DENY_WRITE_DIRS + [d for d in _WINDOWS_SYSTEM if d]:





            allowed = [] if root == _PLUGIN_DIR else _WRITE_ALLOWED_UNDER_DENIED + roots
            if _under(candidate, root) and not any(_under(candidate, ok) for ok in allowed):




                return (f"{expanded} is under {root}, a system folder; write under the project, "
                        "home or temp folder.")
    if not _write_root_ok(real, roots):
        return ("Writes are limited to the project folder, the temp folder, attached files' folders and your "
                "home folder or mounted volumes.")
    if overwrite is False and os.path.isfile(real):
        return (f"{expanded} already exists. Pass overwrite=true only after the user agreed to replace it, "
                "or choose a new file name.")
    return None


def validate_code(code: str) -> str | None:
    """Static check of model-written Python; the real rules live in code_guard."""
    from .code_guard import validate_code as _validate

    return _validate(code)


def build_safe_builtins() -> dict[str, Any]:
    """Builtins for execute_code: guarded import and open, no eval/exec/compile."""
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
    """Hosts the user named in a message may be private LAN addresses (called by the controller)."""
    global _user_text_seen
    _user_text_seen = True
    for match in _HOST_RE.finditer(text or ""):
        _user_hosts.add((match.group(1) or match.group(2) or "").strip("[]").lower())





































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
    """Every address *host* resolves to, briefly cached."""









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
    """Drop the resolution cache. Tests, and a new QGIS session."""
    with _dns_lock:
        _dns_cache.clear()


_LEGACY_IPV4_PART_RE = re.compile(r"^(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)$")


def _legacy_ipv4(text: str):
    """The ``inet_aton`` reading of *text*, or None when it has none."""





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


def _as_address(text: str):
    """The host as an ip address object, or None when it is a name."""




    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return _legacy_ipv4(text)
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped if mapped is not None else address


def _address_is_local(address) -> bool:
    """Loopback, link-local (which is where cloud metadata lives), or a wildcard."""
    return bool(address.is_loopback or address.is_link_local or address.is_unspecified
                or address.is_multicast or address.is_reserved)


def is_local_url(url: str, resolve: bool = True) -> bool:
    """True for a URL on this machine or on a link-local address (cloud metadata, 169.254/16)."""





    try:
        host = (urllib.parse.urlsplit(url).hostname or "").strip("[]").lower()
    except ValueError:
        return True
    if not host:
        return True
    if host in _LOCAL_HOSTS or host.endswith(".localhost"):
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
    """True for an RFC 1918 or ULA address, or a bare hostname without a dot (an intranet name)."""
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").strip("[]").lower()
    except ValueError:
        return False
    if not host:
        return False
    literal = _as_address(host)
    if literal is not None:
        return bool(literal.is_private and not literal.is_loopback and not literal.is_link_local)
    if "." not in host:
        return True
    if not resolve:
        return False


    for text in resolve_host(host):
        address = _as_address(text)
        if address is not None and address.is_private and not address.is_loopback and not address.is_link_local:
            return True
    return False


def vetted_addresses(url: str):
    """The addresses a connection to *url* may use, so the name resolves once."""




















    try:
        host = (urllib.parse.urlsplit(url).hostname or "").strip("[]").lower()
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


def is_paired_backend_url(url: str) -> bool:
    """True when ``url`` is on the very server this plugin talks to."""




    try:
        from .settings import Settings

        parts = urllib.parse.urlsplit(str(url or ""))
        server = urllib.parse.urlsplit(str(Settings().server_url or ""))
    except Exception:  # noqa: BLE001 - no settings yet is not a pairing
        return False
    try:
        host = (parts.hostname or "").strip("[]").lower().rstrip(".")
        theirs = (server.hostname or "").strip("[]").lower().rstrip(".")
        return bool(host) and host == theirs and parts.port == server.port
    except ValueError:
        return False


def validate_url(url: str) -> str | None:
    """None when a tool may fetch ``url``, else one sentence saying why not."""
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
        host = (urllib.parse.urlsplit(text).hostname or "").strip("[]").lower()
        if host not in _user_hosts:
            return (f"{host} is a private network address the user did not name; ask the user for the "
                    "address before fetching it.")
    return None


def qgis_proxy_address() -> str | None:
    """The HTTP proxy QGIS is configured with, as a URL, or None."""










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
    except Exception:  # noqa: BLE001 - no QGIS, or a settings backend that cannot answer
        return None
    if kind not in ("HttpProxy", "HttpCachingProxy") or not host:
        return None




    if excluded and not os.environ.get("no_proxy"):
        os.environ["no_proxy"] = ",".join(x.strip() for x in str(excluded).split("|") if x.strip())
    auth = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(password, safe='')}@" if user else ""
    return f"http://{auth}{host}{':' + port if port else ''}"


def _system_proxy_address() -> str | None:
    """The proxy Windows itself would use, when QGIS holds no proxy of its own."""


















    try:
        from .ws_client import resolve_proxy

        found = resolve_proxy("example.com", 443, True)
    except Exception:  # noqa: BLE001 - no QGIS, or a proxy stack that cannot answer
        return None
    if not found or not found.get("host"):
        return None
    user = str(found.get("user") or "")
    password = str(found.get("password") or "")
    auth = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(password, safe='')}@" if user else ""
    port = found.get("port") or 0
    return f"http://{auth}{found['host']}{':' + str(port) if port else ''}"


def apply_qgis_proxy() -> bool:
    """Point the guarded opener at the QGIS proxy."""
















    from . import net

    net.set_proxy(qgis_proxy_address() or _system_proxy_address())
    _apply_qgis_trust(net)
    return net.proxy_in_use() is not None


def _apply_qgis_trust(net) -> None:
    """Hand the CAs QGIS trusts to the guarded opener. Main thread only."""
    try:
        from .logger import log
        from .ws_client import resolve_tls




        ca_pem, _ = resolve_tls("")
        if net.set_trust(ca_pem):
            log("Tool fetches now also trust the certificates QGIS trusts.")
    except Exception as exc:  # noqa: BLE001 - the system roots alone still work
        try:
            from .logger import log_warning as _warn

            _warn(f"QGIS trusted certificates not read: {exc}")
        except Exception:  # nosec B110 - never let logging break a fetch
            pass
