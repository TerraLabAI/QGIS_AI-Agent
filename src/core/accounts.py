# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings

from . import background, link_vault
from .host_platform import IS_WINDOWS
from .logger import log_warning
from .settings import GROUP
from .tool_registry import tool_error

MARK = "needs_account"

ANSWER = "account:"
_KEY = f"{GROUP}/accounts"

BASIC = "basic"
HEADER = "header"
S3 = "s3"
OAUTH = "oauth"
CLOUD = "cloud"

_lock = threading.Lock()
_call_grants: dict[str, set[str]] = {}
_thread_grants: dict[str, set[str]] = {}
_call_thread: dict[str, str] = {}
_gdal_prefixes: dict[str, list[str]] = {}




_opened: dict[str, tuple[str, dict]] = {}
_transient: set[str] = set()







ENABLED_SETTING = f"{GROUP}/connectedAccountsOn"
ON_BY_DEFAULT: frozenset = frozenset()


def enabled(provider: str = "") -> bool:


    import os
    if os.environ.get("TERRALAB_AGENT_ACCOUNTS", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        value = QgsSettings().value(ENABLED_SETTING, None)
    except Exception:  # noqa: BLE001
        return False

    said = "" if value is None else str(value).strip().lower()
    if said:
        return said in ("1", "true", "yes", "on")
    return provider in ON_BY_DEFAULT


def any_enabled() -> bool:
    return enabled() or any(enabled(key) for key in ON_BY_DEFAULT)


def _host(url: str) -> str:
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    return (parts.hostname or "").lower().rstrip(".")


def _origin(url: str) -> str:


    try:
        parts = urlsplit(str(url or "").strip())
        port = parts.port
    except ValueError:
        return ""
    scheme, host = (parts.scheme or "").lower(), (parts.hostname or "").lower().rstrip(".")
    if not scheme or not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}" + (f":{port}" if port and port != _DEFAULT_PORTS.get(scheme) else "")


def _shown(origin: str) -> str:

    for scheme in ("https://", "postgresql://"):
        if origin.startswith(scheme):
            return origin[len(scheme):]
    return origin


def label_of(key: str, entry: dict | None) -> str:

    entry = entry or {}
    label = str(entry.get("label") or "")
    return _shown(label) if label and label != key else str(entry.get("host") or _shown(key))


def _read() -> dict:
    try:
        raw = QgsSettings().value(_KEY, "", type=str) or ""
        data = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("authcfg")} if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    QgsSettings().setValue(_KEY, json.dumps(data, ensure_ascii=False) if data else "")


def entries() -> dict:




    return _read()


def _note(key: str, **values) -> None:

    data = _read()
    entry = data.get(key)
    if entry is None or all(entry.get(k) == v for k, v in values.items()):
        return
    entry.update(values)
    _write(data)


def worked(key: str) -> None:

    _note(key, worked=True, state="ok")


def refused_store(key: str) -> None:

    _note(key, state="refused")


def expired(entry: dict | None) -> bool:

    if not entry:
        return False
    when = entry.get("expires")
    return bool(entry.get("worked")) or (isinstance(when, (int, float)) and when < time.time())


def token_facts(token: str) -> dict:


    parts = str(token or "").strip().split(".")
    if len(parts) != 3:
        return {}
    try:
        body = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, TypeError):
        return {}
    if not isinstance(body, dict):
        return {}
    facts = {}
    if isinstance(body.get("exp"), (int, float)):
        facts["expires"] = int(body["exp"])
    owner = body.get("uid") or body.get("preferred_username") or body.get("sub")
    if isinstance(owner, str) and 0 < len(owner) <= 64:
        facts["account"] = owner
    return facts





PROVIDERS = {
    "earthdata": {"label": "NASA Earthdata", "host": "urs.earthdata.nasa.gov", "connector": "nasa",
                  "token_page": "https://urs.earthdata.nasa.gov/profile"},  # nosec B105


    "copernicus": {"label": "Copernicus Data Space", "host": "eodata.dataspace.copernicus.eu",
                   "connector": "copernicus",
                   "token_page": "https://eodata-s3keysmanager.dataspace.copernicus.eu/", "keys": S3},  # nosec B105


    "earthengine": {"label": "Earth Engine", "host": "earthengine.googleapis.com", "connector": "earthengine",
                    "keys": OAUTH},


    "googledrive": {"label": "Google Drive", "host": "www.googleapis.com", "connector": "googledrive",
                    "keys": OAUTH, "own": True},
    "onedrive": {"label": "OneDrive and SharePoint", "host": "graph.microsoft.com", "connector": "onedrive",
                 "keys": OAUTH, "own": True},
}
_CDSE_BUCKET = "eodata"
_CDSE_PATH = f"/vsis3/{_CDSE_BUCKET}/"


_CDSE_OPTIONS = f"/vsis3/{_CDSE_BUCKET}"
_S3_ROUTE = ("AWS_S3_ENDPOINT", "AWS_REGION", "AWS_VIRTUAL_HOSTING", "AWS_HTTPS")
_S3_OPTIONS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", *_S3_ROUTE)
_CLOUD_OPTIONS = ("AWS_NO_SIGN_REQUEST", "AZURE_STORAGE_ACCOUNT", "AZURE_STORAGE_ACCESS_KEY",
                  "AZURE_STORAGE_SAS_TOKEN", "AZURE_NO_SIGN_REQUEST", "GS_ACCESS_KEY_ID", "GS_SECRET_ACCESS_KEY",
                  "GS_NO_SIGN_REQUEST", "GS_OAUTH2_PRIVATE_KEY", "GS_OAUTH2_CLIENT_EMAIL", "GS_OAUTH2_SCOPE")
_GDAL_OPTIONS = ("GDAL_HTTP_AUTH", "GDAL_HTTP_USERPWD", "GDAL_HTTP_HEADERS", *_S3_OPTIONS, *_CLOUD_OPTIONS)

_CLOUD_META = ("store", "bucket", "account", "endpoint", "region", "https", "secret", "vendor", "address")


_PATHS_OPTION = "TERRALAB_AGENT_ACCOUNT_PATHS"
_DEFAULT_PORTS = {"https": 443, "http": 80, "postgresql": 5432}


def cdse_key(url: str) -> str:


    text = str(url or "").strip()
    if text.startswith(f"s3://{_CDSE_BUCKET}/"):
        return text[len(f"s3://{_CDSE_BUCKET}/"):]
    parts = urlsplit(text)
    if parts.scheme != "https" or (parts.hostname or "").lower() != PROVIDERS["copernicus"]["host"]:
        return ""
    from urllib.parse import unquote
    path = unquote(parts.path).lstrip("/")
    return path[len(_CDSE_BUCKET) + 1:] if path.startswith(_CDSE_BUCKET + "/") else ""


def provider_of(url: str) -> str:


    from . import accounts_drive, geodata_rules
    drive = accounts_drive.provider_of(url)
    if drive:
        return drive
    if cdse_key(url):
        return "copernicus"
    if not str(url or "").strip().lower().startswith("https://"):
        return ""
    host = _host(url)
    if host and (host in geodata_rules.rule("stac", "signed_hosts")
                 or host.endswith(tuple(geodata_rules.rule("stac", "signed_suffixes")))):
        return "earthdata"
    return ""


def key_for(url: str) -> str:

    return provider_of(url) or _origin(url)


def entry_for(url: str) -> tuple[str, dict] | tuple[str, None]:

    key = key_for(url)
    entry = _read().get(key) if key else None
    return key, entry


def _shown_host(url: str) -> str:
    provider = provider_of(url)
    return PROVIDERS[provider]["label"] if provider else _shown(_origin(url))





def bind_call(tool_call_id: str, thread_id: str) -> None:

    with _lock:
        _call_thread[str(tool_call_id)] = str(thread_id or "")


def release_call(tool_call_id: str) -> None:

    with _lock:
        _call_thread.pop(str(tool_call_id), None)
        _call_grants.pop(str(tool_call_id), None)


def grant(key: str, tool_call_id: str, scope: str, thread_id: str = "") -> None:

    if not key:
        return
    with _lock:
        if scope == "thread" and thread_id:
            _thread_grants.setdefault(str(thread_id), set()).add(key)
        else:
            _call_grants.setdefault(str(tool_call_id), set()).add(key)
    entry = entries().get(key) or {}
    if entry.get("authcfg") and background.on_main_thread():
        _load(str(entry["authcfg"]))


def always(key: str) -> bool:


    return bool(key) and (_read().get(key) or {}).get("always") is True


def set_always(key: str, on: bool) -> None:


    _note(key, always=bool(on))
    if not on:
        with _lock:
            for keys in _thread_grants.values():
                keys.discard(key)


def granted(key: str) -> bool:
    call = background.current_call()
    if not key or not call:
        return False
    with _lock:
        return key in _call_grants.get(call, ()) or key in _thread_grants.get(_call_thread.get(call, "\0"), ())


def authcfg_for(url: str) -> str:

    key, entry = entry_for(url)
    return str(entry["authcfg"]) if entry and granted(key) else ""


def check(url: str, what: str = "") -> dict | None:


    provider = provider_of(url)
    if not enabled(provider or ""):
        return None
    if provider and not PROVIDERS[provider].get("own"):


        return None
    key, entry = entry_for(url)
    if entry and not granted(key):
        return consent(url, what)
    return None


def consent(url: str, what: str = "") -> dict:
    key, entry = entry_for(url)
    return {MARK: {"kind": "consent", "key": key, "host": _shown_host(url),
                   "label": label_of(key, entry), "what": str(what or "")[:200]},
            "failure": tool_error("The user did not allow their saved access to this service.",
                                  "PERMISSION_DENIED", hint="account_consent_denied", host=_host(url))}


def spent(url: str, message: str, what: str = "") -> dict | None:



    key, entry = entry_for(url)
    failure = tool_error(message or "TerraLab's shared access to this archive is busy for now.",
                         "EXECUTION_FAILED", host=_host(url))
    if entry:
        if granted(key):
            return None
        return {**consent(url, what), "failure": failure}
    return {MARK: {"kind": "provider", "key": key, "provider": key, "host": _shown_host(url),
                   "label": PROVIDERS.get(key, {}).get("label") or key, "reason": "shared_spent"},
            "failure": failure}


def refused(url: str, status: int, failure: dict, service: str = "", token_header: str = "",  # nosec B107
            connector: str = "") -> dict:



    key, entry = entry_for(url)
    used = bool(entry and granted(key))
    provider = provider_of(url)
    if int(status or 0) == 404 and used and provider == "googledrive":

        from . import accounts_drive
        return {MARK: {"kind": "pick", "key": key, "provider": provider, "host": _shown_host(url), "url": url,
                       "connector": PROVIDERS[provider]["connector"], "label": PROVIDERS[provider]["label"],
                       "file_id": accounts_drive.file_id(url)},
                "failure": failure}
    if int(status or 0) != 401 and not used:


        return failure
    kind = "retry" if used else ("provider" if provider else "sign_in")
    if used:
        _note(key, state="refused")

    return {MARK: {"kind": kind, "key": key or _host(url), "host": _shown_host(url), "provider": provider,
                   "url": url, "expired": bool(used and expired(entry)),
                   "token_header": str(token_header or ""),
                   "connector": str(connector or PROVIDERS.get(provider, {}).get("connector") or ""),
                   "label": PROVIDERS.get(provider, {}).get("label") or "",
                   "status": int(status or 0), "service": str(service or "")[:40],
                   "reason": "sharepoint" if provider == "onedrive" and _needs_sites(url) else "",
                   "username": ("" if not used or entry.get("kind") not in (BASIC, HEADER)
                                else _username(str(entry["authcfg"])))},
            "failure": failure}


def is_refusal(status, url: str = "") -> bool:


    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    return status in (401, 403) or (status == 404 and bool(url) and provider_of(url) == "googledrive")


def _needs_sites(url: str) -> bool:
    from . import accounts_drive
    return accounts_drive.needs_sites_scope(url)


def uri_with_auth(uri: str, url: str, style: str = "amp") -> str:


    cfg = authcfg_for(url)
    if not cfg or "authcfg" in uri:
        return uri
    return f"{uri}&authcfg={cfg}" if style == "amp" else f"{uri} authcfg='{cfg}'"


def headers_for(url: str) -> dict:

    cfg = authcfg_for(url)
    key, entry = entry_for(url)
    if cfg and PROVIDERS.get(key, {}).get("own") and (entry or {}).get("kind") == OAUTH:
        from . import accounts_drive
        return accounts_drive.headers(key)
    if not cfg or (entry or {}).get("kind") not in (BASIC, HEADER):
        return {}
    loaded = _load(cfg)
    if loaded is None:
        return {}
    method, values = loaded
    if method == "Basic":
        pair = f"{values.get('username', '')}:{values.get('password', '')}".encode()
        return {"Authorization": "Basic " + base64.b64encode(pair).decode("ascii")}
    return {k: v for k, v in values.items() if k and v}


def signed(address: str, url: str = "", headers: dict | None = None):



    request = urllib.request.Request(address, headers=dict(headers or {}))
    access = headers_for(url or address)
    for key, value in access.items():
        request.add_unredirected_header(key, value)
    return request, bool(access)


def allow_gdal(url: str) -> bool:




    cfg = authcfg_for(url)
    if not cfg:
        return False
    loaded = _load(cfg)
    if loaded is None:
        return False
    try:
        from osgeo import gdal
    except ImportError:
        return False
    if not hasattr(gdal, "SetPathSpecificOption"):
        return False
    key, entry = entry_for(url)
    method, values = loaded
    if (entry or {}).get("kind") == S3:
        obj = cdse_key(url)
        if not obj:
            return False
        path = f"{_CDSE_PATH}{obj}"
        endpoint = PROVIDERS["copernicus"]["host"]
        for option, value in (("AWS_S3_ENDPOINT", endpoint), ("AWS_REGION", "default"),
                              ("AWS_VIRTUAL_HOSTING", "FALSE"), ("AWS_HTTPS", "YES")):
            gdal.SetPathSpecificOption(_CDSE_OPTIONS, option, value)
        if gdal.GetPathSpecificOption(path, "AWS_S3_ENDPOINT", None) != endpoint:

            _clear_gdal(gdal, [_CDSE_OPTIONS])
            return False
        gdal.SetPathSpecificOption(path, "AWS_ACCESS_KEY_ID", values.get("username", ""))
        gdal.SetPathSpecificOption(path, "AWS_SECRET_ACCESS_KEY", values.get("password", ""))
        _remember(gdal, key, [path, _CDSE_OPTIONS])
        return True
    if not _origin(url) or (entry or {}).get("kind") not in (BASIC, HEADER):
        return False
    prefixes = [f"/vsicurl/{url}", url]
    for prefix in prefixes:
        if method == "Basic":
            gdal.SetPathSpecificOption(prefix, "GDAL_HTTP_AUTH", "BASIC")
            gdal.SetPathSpecificOption(prefix, "GDAL_HTTP_USERPWD",
                                       f"{values.get('username', '')}:{values.get('password', '')}")
        else:
            gdal.SetPathSpecificOption(prefix, "GDAL_HTTP_HEADERS",
                                       "\r\n".join(f"{k}: {v}" for k, v in values.items() if k and v))
    _remember(gdal, key, prefixes)
    return True


def give_gdal(key: str, route_path: str, route: dict, path: str, from_vault: dict, literal: dict,
              fence: dict | None = None) -> bool:







    entry = entries().get(key) or {}
    if entry.get("kind") != CLOUD or not granted(key):
        return False
    loaded = _load(str(entry.get("authcfg") or ""))
    if loaded is None:
        return False
    try:
        from osgeo import gdal
    except ImportError:
        return False
    if not hasattr(gdal, "SetPathSpecificOption"):
        return False
    free_route(route_path, key)
    values = loaded[1]
    for prefix in (route_path, path):
        for option, value in route.items():
            gdal.SetPathSpecificOption(prefix, option, value)
    for option, value in literal.items():
        gdal.SetPathSpecificOption(path, option, value)
    for option, field in from_vault.items():
        gdal.SetPathSpecificOption(path, option, str(values.get(field) or ""))
    fence = fence or {}
    for prefix, options in fence.items():
        for option, value in options.items():
            gdal.SetPathSpecificOption(prefix, option, value)
    wanted = {route_path: route, path: {**route, **literal}, **fence}
    if any(gdal.GetPathSpecificOption(prefix, option, None) != value
           for prefix, options in wanted.items() for option, value in options.items()):
        _clear_gdal(gdal, [route_path, path, *fence])
        return False
    _remember(gdal, key, [route_path, path, *fence])
    if hasattr(gdal, "VSICurlPartialClearCache"):

        gdal.VSICurlPartialClearCache(path)
    else:
        gdal.VSICurlClearCache()
    return True


def free_route(route_path: str, key: str = "") -> None:

    with _lock:
        others = [k for k, paths in _gdal_prefixes.items() if k != key and route_path in paths]
    for other in others:
        drop_gdal(other)


def take_back_gdal(key: str, prefixes) -> None:

    prefixes = list(prefixes)
    with _lock:
        known = _gdal_prefixes.get(key, [])
        _gdal_prefixes[key] = [p for p in known if p not in prefixes]
    try:
        from osgeo import gdal
        _clear_gdal(gdal, prefixes)
        _remember(gdal, "", [])
    except Exception:  # nosec B110
        pass


def drop_gdal(key: str) -> None:

    with _lock:
        prefixes = _gdal_prefixes.pop(key, [])
    if not prefixes:
        return
    try:
        from osgeo import gdal
        _clear_gdal(gdal, prefixes)
        _remember(gdal, "", [])
        gdal.VSICurlClearCache()
    except Exception:  # nosec B110
        pass


def _remember(gdal, key: str, prefixes: list) -> None:
    with _lock:
        if key:
            known = _gdal_prefixes.setdefault(key, [])
            known.extend(p for p in prefixes if p not in known)
        every = sorted({p for paths in _gdal_prefixes.values() for p in paths})
    gdal.SetConfigOption(_PATHS_OPTION, json.dumps(every) if every else None)


def clear_gdal_paths(prefixes) -> None:

    from osgeo import gdal
    _clear_gdal(gdal, prefixes)


def _clear_gdal(gdal, prefixes) -> None:
    for prefix in prefixes:
        for option in _GDAL_OPTIONS:
            gdal.SetPathSpecificOption(prefix, option, None)


def forget_gdal() -> None:


    try:
        from osgeo import gdal
    except ImportError:
        return
    with _lock:
        paths = {p for known in _gdal_prefixes.values() for p in known}
        _gdal_prefixes.clear()
    try:
        paths.update(p for p in json.loads(gdal.GetConfigOption(_PATHS_OPTION) or "[]") if isinstance(p, str))
    except ValueError:
        pass
    _clear_gdal(gdal, paths)
    gdal.SetConfigOption(_PATHS_OPTION, None)
    if paths:
        gdal.VSICurlClearCache()


def gdal_path(url: str) -> str:


    key = cdse_key(url)
    return f"{_CDSE_PATH}{key}" if key and allow_gdal(url) else ""


def _manager():
    try:
        return QgsApplication.authManager()
    except Exception:  # noqa: BLE001
        return None


def vault_ready(prompt: bool = False) -> bool:


    am = _manager()
    if am is None:
        return False
    try:
        return bool(am.masterPasswordIsSet() or (prompt and am.setMasterPassword(True)))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"QGIS authentication database not opened: {exc}")
        return False


def vault_has_master() -> bool:

    am = _manager()
    try:
        return bool(am is not None and am.masterPasswordHashInDatabase())
    except Exception:  # noqa: BLE001
        return True


def _clear_ssl_errors() -> None:

    import _ssl
    import ctypes
    libraries = []
    try:
        libraries.append(ctypes.CDLL(_ssl.__file__))
    except OSError:
        pass
    if IS_WINDOWS:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        for name in ("libcrypto-3-x64.dll", "libcrypto-3.dll", "libcrypto-1_1-x64.dll", "libcrypto-1_1.dll"):
            if kernel32.GetModuleHandleW(name):
                try:
                    libraries.append(ctypes.CDLL(name))
                except OSError:
                    pass
    for library in libraries:
        try:
            library.ERR_clear_error()
        except AttributeError:
            pass


def _load(cfg_id: str) -> tuple[str, dict] | None:
    with _lock:
        if cfg_id in _opened:
            return _opened[cfg_id]
    if not background.on_main_thread():
        return None
    am = _manager()
    if am is None or not vault_ready():
        return None
    cfg = QgsAuthMethodConfig()
    try:
        ok = am.loadAuthenticationConfig(cfg_id, cfg, True)
        ok = ok[0] if isinstance(ok, tuple) else ok
    except Exception:  # noqa: BLE001
        return None
    finally:
        _clear_ssl_errors()
    if not ok:
        return None
    opened = (cfg.method(), dict(cfg.configMap()))
    with _lock:
        _opened[cfg_id] = opened
    return opened


HIDDEN = "[your saved access, hidden]"


def _secret_strings() -> list[str]:




    with _lock:
        opened = list(_opened.values())
        found = set(_transient)
    for method, values in opened:
        for name, value in values.items():
            value = str(value or "").strip()
            lowered = str(name).lower()
            if method == "Basic" and lowered == "password":
                found.add(value)
                pair = f"{values.get('username', '')}:{value}".encode()
                found.add(base64.b64encode(pair).decode("ascii"))
            elif method == "APIHeader" or any(w in lowered for w in ("secret", "token", "password", "key")):
                found.add(value)
                if value.lower().startswith(("bearer ", "token ")):
                    found.add(value.split(" ", 1)[1].strip())
    return sorted((v for v in found if len(v) >= 8), key=len, reverse=True)


def hide_also(token: str) -> None:


    token = str(token or "").strip()
    if len(token) >= 8:
        with _lock:
            _transient.add(token)


def hide_secrets(result):



    result = link_vault.hide(result)
    secrets = _secret_strings()
    if not secrets:
        return result

    def walk(value):
        if isinstance(value, str):
            for secret in secrets:
                if secret in value:
                    value = value.replace(secret, HIDDEN)
            return value
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value
    return walk(result)


def _username(cfg_id: str) -> str:
    loaded = _load(cfg_id)
    return str(loaded[1].get("username", "")) if loaded and loaded[0] == "Basic" else ""


def store_token(provider: str, token: str, host: str = "", header: str = "Authorization") -> str:


    token = str(token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token or not (provider in PROVIDERS or host):
        return ""
    values = {"header": header or "Authorization", "value": f"Bearer {token}"}
    facts = token_facts(token)
    if provider in PROVIDERS:
        return store(f"https://{provider}/", HEADER, values, PROVIDERS[provider]["label"], key=provider, facts=facts)
    origin = host if "://" in host else f"https://{host}"
    return store(origin, HEADER, values, _shown(_origin(origin)), facts=facts)


def store_s3_keys(provider: str, access_key: str, secret_key: str, extra: dict | None = None, via: str = "",
                  facts: dict | None = None, renewal: bool = False) -> str:


    if (PROVIDERS.get(provider) or {}).get("keys") != S3:
        return ""
    return store(f"https://{provider}/", S3, {"username": str(access_key or "").strip(),
                                              "password": str(secret_key or "").strip()},
                 PROVIDERS[provider]["label"], key=provider, extra=extra, via=via, facts=facts, renewal=renewal)


def store_oauth(provider: str, refresh_token: str, account: str = "") -> str:

    if (PROVIDERS.get(provider) or {}).get("keys") != OAUTH or not str(refresh_token or "").strip():
        return ""
    return store(f"https://{provider}/", OAUTH, {"username": str(account or provider),
                                                 "password": str(refresh_token).strip()},
                 PROVIDERS[provider]["label"], key=provider)


def refresh_token(key: str, acting: bool = False) -> str:



    entry = _read().get(key) or {}
    if entry.get("kind") != OAUTH or not (granted(key) or (acting and background.on_main_thread())):
        return ""
    loaded = _load(str(entry["authcfg"]))
    return str(loaded[1].get("password") or "") if loaded and loaded[0] == "Basic" else ""


def store(url: str, kind: str, values: dict, label: str = "", key: str = "", facts: dict | None = None,
          extra: dict | None = None, via: str = "", meta: dict | None = None, renewal: bool = False) -> str:







    key = key or key_for(url)
    if not key or kind not in (BASIC, HEADER, S3, OAUTH, CLOUD) or not vault_ready(prompt=True):
        return ""
    cfg = QgsAuthMethodConfig()
    cfg.setName(f"TerraLab AI Agent: {label or key}")
    if kind in (BASIC, S3, OAUTH, CLOUD):
        user, password = str(values.get("username") or ""), str(values.get("password") or "")
        if (not user and not password) or (kind in (S3, OAUTH, CLOUD) and not (user and password)):
            return ""
        cfg.setMethod("Basic")
        cfg.setConfig("username", user)
        cfg.setConfig("password", password)
        for name, value in (extra or {}).items():
            if name not in ("username", "password", "realm") and value:
                cfg.setConfig(str(name), str(value))
    else:
        name, value = str(values.get("header") or "").strip(), str(values.get("value") or "").strip()
        if not name or not value:
            return ""
        cfg.setMethod("APIHeader")
        cfg.setConfig(name, value)
    am = _manager()
    try:
        ok = am.storeAuthenticationConfig(cfg)
        ok = ok[0] if isinstance(ok, tuple) else ok
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Access for {key} not saved: {exc}")
        return ""
    finally:
        _clear_ssl_errors()
    if not ok or not cfg.id():
        return ""
    with _lock:
        _opened[cfg.id()] = (cfg.method(), dict(cfg.configMap()))
    data = _read()
    previous = (data.get(key) or {}).get("authcfg")


    replaced = vault_values(key) if (data.get(key) or {}).get("via") == "browser" and via != "browser" else {}
    shown = PROVIDERS.get(key, {}).get("host") or str((meta or {}).get("host") or "") or _shown(key)
    data[key] = {"authcfg": cfg.id(), "kind": kind, "label": label or shown or key, "host": shown, "state": "ok",
                 **{k: v for k, v in (facts or {}).items() if k in ("account", "expires")},
                 **({"via": via} if via else {}),
                 **{name: (meta or {})[name] for name in _CLOUD_META if kind == CLOUD and name in (meta or {})},
                 **({"always": True} if renewal and (data.get(key) or {}).get("always") else {})}
    if kind == HEADER and values.get("header"):
        data[key]["header"] = str(values["header"])[:64]
    if kind == BASIC and values.get("username"):
        data[key].setdefault("account", str(values["username"])[:64])
    drop_gdal(key)
    _write(data)
    if previous and previous != cfg.id():
        _remove_config(previous)
    if replaced:
        from . import accounts_oauth
        accounts_oauth.forget(key, replaced)
        replaced.clear()
    return key


def _remove_config(cfg_id: str) -> None:
    with _lock:
        _opened.pop(cfg_id, None)
    am = _manager()
    try:
        if am is not None:
            am.removeAuthenticationConfig(cfg_id)
    except Exception:  # nosec B110
        pass


def vault_values(key: str) -> dict:

    entry = _read().get(key) or {}
    loaded = _load(str(entry.get("authcfg") or "")) if entry else None
    return dict(loaded[1]) if loaded else {}


def remove(key: str) -> None:



    signed_in = (_read().get(key) or {}).get("via") == "browser"
    browser = vault_values(key) if signed_in and vault_ready(prompt=True) else {}
    data = _read()
    entry = data.pop(key, None)
    _write(data)
    if entry:
        _remove_config(str(entry.get("authcfg") or ""))
    if browser:
        from . import accounts_oauth
        accounts_oauth.forget(key, browser)
        browser.clear()
    with _lock:
        for keys in (*_call_grants.values(), *_thread_grants.values()):
            keys.discard(key)
    drop_gdal(key)
