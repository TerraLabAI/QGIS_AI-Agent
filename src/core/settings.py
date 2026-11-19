# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import uuid

from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings
from qgis.PyQt.QtCore import QSysInfo

from .host_platform import IS_WINDOWS, retry_file_op
from .i18n import UI_LANGUAGES, current_locale
from .net_state import clear_cache
from .plan import effort_default
from .policy import state_dir  # noqa: F401

GROUP = "TerraLab/AIAgent"
DEFAULT_SERVER_URL = "wss://agent.terra-lab.ai/ws"
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def profile_settings():








    return QgsSettings()


UNSAVED_KEY = "(unsaved)"


def server_url_problem(url: str) -> str | None:






    from urllib.parse import urlsplit

    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return "The server URL cannot be parsed."
    scheme = parts.scheme.lower()
    if scheme == "wss":
        return None
    if scheme == "ws":
        if (parts.hostname or "").lower() in _LOOPBACK_HOSTS:
            return None
        return "A plain ws:// server is only accepted on this machine; use wss:// for a remote server."
    return "The server URL must start with wss://."










ALTERNATE_AFTER_FAILURES = 3
_ALTERNATE_DOMAIN = ".terra-lab.ai"
_ALTERNATES_KEPT = 4
_alternates: dict = {"served": (), "active": ""}


def _alternate_ok(url: str) -> bool:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme.lower() == "wss" and host.endswith(_ALTERNATE_DOMAIN) and not parts.username
            and port in (None, 443))


def set_served_server_urls(urls) -> None:

    kept: list[str] = []
    for url in urls if isinstance(urls, (list, tuple)) else ():
        url = url.strip() if isinstance(url, str) else ""
        if url and url != DEFAULT_SERVER_URL and url not in kept and _alternate_ok(url):
            kept.append(url)
    _alternates["served"] = tuple(kept[:_ALTERNATES_KEPT])


def server_url_choices() -> tuple:

    return (DEFAULT_SERVER_URL, *_alternates["served"])


def alternate_server_url() -> str:

    active = _alternates["active"]
    return active if active in _alternates["served"] else ""


def use_server_url(url: str) -> None:

    _alternates["active"] = url if url in _alternates["served"] else ""


KEY_RE = re.compile(r"^tl_[0-9a-f]{32}$")



PROFILE_LINE_MAX_CHARS = 80

_AUTHCFG_KEY = "authcfg_id"




_LEGACY_KEY = "activation_key"
_PROTECTED_KEY = "activation_key_dpapi"
_DPAPI_PREFIX = "dpapi1:"
_DATA_ACCESS_KEY = "data_access"
_ACCOUNT_TICKET_KEY = "account_ticket"
_DPAPI_ENTROPY = b"TerraLab AI Agent activation key"


def _dpapi(data: bytes, seal: bool) -> bytes | None:

    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        def _blob(raw: bytes):
            buffer = ctypes.create_string_buffer(raw, len(raw))
            return _Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        source, _keep = _blob(data)
        entropy, _keep_entropy = _blob(_DPAPI_ENTROPY)
        out = _Blob()
        call = crypt32.CryptProtectData if seal else crypt32.CryptUnprotectData

        if not call(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out)):
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
    except Exception:  # noqa: BLE001
        return None


def _protect(key: str) -> str:

    import base64

    sealed = _dpapi(key.encode("utf-8"), seal=True)
    return _DPAPI_PREFIX + base64.b64encode(sealed).decode("ascii") if sealed else ""


def _unprotect(value: str) -> str:

    import base64

    if not value.startswith(_DPAPI_PREFIX):
        return ""
    try:
        opened = _dpapi(base64.b64decode(value[len(_DPAPI_PREFIX):]), seal=False)
        return opened.decode("utf-8") if opened else ""
    except (ValueError, UnicodeDecodeError):
        return ""


_DEVICE_SEED_KEY = "TerraLab/device_seed"
_DEVICE_HASH_LEN = 16

_device_hash_cache: str | None = None











_ACCOUNT_DIR_LEN = 16
_MOVED_ON_UPGRADE = ("threads", "snapshots", "traces", "idempotency.json")
_account_tag_cache: str | None = None


def reset_account_tag_cache() -> None:






    global _account_tag_cache
    _account_tag_cache = None
    clear_cache()


def account_tag(key: str = "") -> str:

    global _account_tag_cache
    if key:
        return "a" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:_ACCOUNT_DIR_LEN]
    if _account_tag_cache is None:
        try:
            current = Settings().activation_key
        except Exception:  # noqa: BLE001
            current = ""
        _account_tag_cache = (
            "a" + hashlib.sha256(current.encode("utf-8")).hexdigest()[:_ACCOUNT_DIR_LEN]
            if current else "signed-out"
        )
    return _account_tag_cache


def account_dir() -> str:





    folder = os.path.join(state_dir(), "accounts", account_tag())
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return folder
    _adopt_legacy_state(folder)
    return folder


def _adopt_legacy_state(folder: str) -> None:






    marker = os.path.join(folder, ".adopted")
    if os.path.exists(marker):
        return
    base = state_dir()
    try:
        for name in _MOVED_ON_UPGRADE:
            source = os.path.join(base, name)
            target = os.path.join(folder, name)
            if os.path.exists(source) and not os.path.exists(target):

                retry_file_op(os.replace, source, target)
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("moved from the shared folder on first run of this version\n")
    except OSError:
        pass


def key_prefix(key: str) -> str:

    return (key or "")[:6]







_cache_memory: dict[str, str] = {}


def _cache_path(name: str) -> str:
    profile = hashlib.sha1(QgsApplication.qgisSettingsDirPath().encode("utf-8"),
                           usedforsecurity=False).hexdigest()[:10]
    return os.path.join(state_dir(), "server-cache", f"{profile}-{name}.json")



CRASH_ASKED_KEPT = 20


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]



PANEL_RETURN_DAYS = 7


_RETIRED_CLEARED = [False]


class Settings:
    def __init__(self, settings=None):


        self._cache_files = settings is None
        self._s = settings or profile_settings()
        self._session_effort = ""
        if settings is None and not _RETIRED_CLEARED[0]:
            _RETIRED_CLEARED[0] = True


            for name in ("question_policy", "expertise", "units"):
                if self._s.contains(self._key(name)):
                    self._s.remove(self._key(name))


            if self._s.contains(self._key("notify_volume")):
                if self._get("notify_volume", "") in ("0", "0.0"):
                    self._set("notify_mode", "off")
                self._s.remove(self._key("notify_volume"))
            if self._get("notify_mode", "") == "icon":
                self._set("notify_mode", "sound")

    def _cache_get(self, name: str) -> str:
        if not self._cache_files:
            return self._get(name, "")
        if name in _cache_memory:
            return _cache_memory[name]
        path = _cache_path(name)
        try:
            with open(path, encoding="utf-8") as fh:
                value = fh.read()
        except OSError:
            value = self._get(name, "")
            if value:
                self._cache_set(name, value)
        _cache_memory[name] = value
        return value

    def _cache_set(self, name: str, value: str) -> None:
        if not self._cache_files:
            self._set(name, value)
            return
        if _cache_memory.get(name) == value and not self._s.contains(self._key(name)):
            return
        path = _cache_path(name)
        try:
            from .writeback import write_atomic

            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_atomic(path, value)
        except OSError:

            self._set(name, value)
            _cache_memory[name] = value
            return
        _cache_memory[name] = value
        if self._s.contains(self._key(name)):
            self._s.remove(self._key(name))


    @staticmethod
    def _key(name: str) -> str:
        return f"{GROUP}/{name}"

    def _get(self, name: str, default: str = "") -> str:
        try:
            value = self._s.value(self._key(name), default, type=str)
        except Exception:  # nosec B110
            value = self._s.value(self._key(name), default)
        return value if isinstance(value, str) else (default if value is None else str(value))

    def _set(self, name: str, value) -> None:





        key = self._key(name)
        if isinstance(value, str):
            try:
                if self._s.contains(key) and self._s.value(key) == value:
                    return
            except Exception:  # nosec B110
                pass
        self._s.setValue(key, value)

    @property
    def last_run_version(self) -> str:

        return self._get("last_run_version", "")

    @last_run_version.setter
    def last_run_version(self, value: str) -> None:
        self._set("last_run_version", str(value or ""))

    @property
    def update_pressed(self) -> str:

        return self._get("update_pressed", "")

    @update_pressed.setter
    def update_pressed(self, value: str) -> None:
        self._set("update_pressed", str(value or ""))

    @property
    def signed_out_mark(self) -> str:

        return self._get("signed_out_mark", "")

    @signed_out_mark.setter
    def signed_out_mark(self, value: str) -> None:
        self._set("signed_out_mark", str(value or ""))

    @property
    def last_paid(self) -> bool:



        return self._get("last_paid", "0") == "1"

    @last_paid.setter
    def last_paid(self, value: bool) -> None:
        self._set("last_paid", "1" if value else "0")

    def feedback_asks_shown(self, now: float) -> list[list]:

        try:
            data = json.loads(self._get("feedback_asks_shown", "") or "[]")
        except ValueError:
            return []
        return [row for row in data if isinstance(row, list) and len(row) == 2
                and isinstance(row[0], (int, float)) and 0 <= now - row[0] < 7 * 86400]

    def note_feedback_ask_shown(self, now: float, reason: str) -> None:
        self._set("feedback_asks_shown", json.dumps(self.feedback_asks_shown(now) + [[int(now), reason]]))

    def crash_asked(self, key: str) -> bool:


        return _short_hash(key) in self._crash_asked()

    def note_crash_asked(self, key: str) -> None:
        kept = [k for k in self._crash_asked() if k != _short_hash(key)] + [_short_hash(key)]
        self._set("crash_asked", json.dumps(kept[-CRASH_ASKED_KEPT:]))

    def _crash_asked(self) -> list:
        try:
            data = json.loads(self._get("crash_asked", "") or "[]")
        except ValueError:
            return []
        return [k for k in data if isinstance(k, str)] if isinstance(data, list) else []

    def plan_seen(self, tag: str) -> str:


        return self._get(f"plan_seen/{tag}", "")

    def set_plan_seen(self, tag: str, plan: str) -> None:
        self._set(f"plan_seen/{tag}", plan)


    @property
    def dock_visible(self) -> bool:


        return self._get("dock_visible", "1") == "1"

    @dock_visible.setter
    def dock_visible(self, value: bool) -> None:
        self._set("dock_visible", "1" if value else "0")

    @property
    def panel_return_until(self) -> float:


        try:
            return float(self._get("panel_return_until", "0") or 0)
        except ValueError:
            return 0.0

    @panel_return_until.setter
    def panel_return_until(self, value: float) -> None:
        self._set("panel_return_until", str(int(value)))

    @property
    def onboarded(self) -> bool:

        return self._get("onboarded", "0") == "1"

    @onboarded.setter
    def onboarded(self, value: bool) -> None:
        self._set("onboarded", "1" if value else "0")


    @property
    def server_url(self) -> str:







        configured = self.configured_server_url
        if configured == DEFAULT_SERVER_URL:
            return alternate_server_url() or configured
        return configured

    @server_url.setter
    def server_url(self, value: str) -> None:
        value = (value or "").strip()
        if value and value == alternate_server_url():
            value = DEFAULT_SERVER_URL
        self._set("server_url", value or DEFAULT_SERVER_URL)

    @property
    def configured_server_url(self) -> str:

        stored = self._get("server_url", DEFAULT_SERVER_URL) or DEFAULT_SERVER_URL
        return stored if server_url_problem(stored) is None else DEFAULT_SERVER_URL










    @property
    def known_connectors(self) -> list:







        raw = self._cache_get("known_connectors")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return [r for r in data if isinstance(r, dict) and r.get("id")] if isinstance(data, list) else []

    @known_connectors.setter
    def known_connectors(self, rows) -> None:
        clean = [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]
        self._cache_set("known_connectors", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def known_qgis_plugins(self) -> list:






        raw = self._cache_get("known_qgis_plugins")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return [r for r in data if isinstance(r, dict) and r.get("folder")] if isinstance(data, list) else []

    @known_qgis_plugins.setter
    def known_qgis_plugins(self, rows) -> None:
        clean = [r for r in (rows or []) if isinstance(r, dict) and r.get("folder")]
        self._cache_set("known_qgis_plugins", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def known_basemaps(self) -> list:





        raw = self._cache_get("known_basemaps")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return [r for r in data if isinstance(r, dict) and r.get("id")] if isinstance(data, list) else []

    @known_basemaps.setter
    def known_basemaps(self, rows) -> None:
        clean = [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]
        self._cache_set("known_basemaps", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def known_use_cases(self) -> list:







        raw = self._cache_get("known_use_cases")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return [r for r in data if isinstance(r, dict) and r.get("id")] if isinstance(data, list) else []

    @known_use_cases.setter
    def known_use_cases(self, rows) -> None:
        clean = [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]
        self._cache_set("known_use_cases", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def known_use_case_groups(self) -> list:

        raw = self._cache_get("known_use_case_groups")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return [r for r in data if isinstance(r, dict) and r.get("id")] if isinstance(data, list) else []

    @known_use_case_groups.setter
    def known_use_case_groups(self, rows) -> None:
        clean = [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]
        self._cache_set("known_use_case_groups", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def hidden_plugins(self) -> list:






        raw = self._cache_get("hidden_plugins")
        return [part for part in (piece.strip() for piece in raw.split(",")) if part]

    @hidden_plugins.setter
    def hidden_plugins(self, folders) -> None:
        clean = sorted({str(f).strip() for f in (folders or []) if str(f or "").strip()})
        self._cache_set("hidden_plugins", ",".join(clean))



    CHOICES = {
        "mode": ("agent", "ask"),
        "approval": ("ask", "careful", "auto"),
        "reply_style": ("balanced", "concise", "detailed"),
        "layer_naming": ("human", "snake_case"),
        "send_shortcut": ("enter", "modifier"),
    }

    def _choice(self, name: str) -> str:
        allowed = self.CHOICES[name]
        value = self._get(name, allowed[0])
        return value if value in allowed else allowed[0]

    def _set_choice(self, name: str, value: str) -> None:
        allowed = self.CHOICES[name]
        self._set(name, value if value in allowed else allowed[0])


    @property
    def mode(self) -> str:
        return self._choice("mode")

    @mode.setter
    def mode(self, value: str) -> None:
        self._set_choice("mode", value)

    EFFORTS = ("low", "medium", "high")

    @property
    def effort(self) -> str:






        return self._session_effort or effort_default()

    @effort.setter
    def effort(self, value: str) -> None:
        self._session_effort = value if value in self.EFFORTS else ""

    def reset_effort(self) -> bool:

        if not self._session_effort:
            return False
        self._session_effort = ""
        return True

    @property
    def approval(self) -> str:




        return self._choice("approval")

    @approval.setter
    def approval(self, value: str) -> None:
        self._set_choice("approval", value)

    def forget_autopilot(self) -> bool:

        if self.approval != "auto":
            return False
        self.approval = "ask"
        return True



    _stored_locale_dropped = False

    def _drop_stored_locale(self) -> None:






        if Settings._stored_locale_dropped:
            return
        Settings._stored_locale_dropped = True
        try:
            if not self._get("locale", ""):
                return
            self._s.remove(self._key("locale"))
            self._s.sync()
        except Exception:  # nosec B110
            pass

    @property
    def locale(self) -> str:

        self._drop_stored_locale()
        return current_locale()


    @property
    def ui_language(self) -> str:

        value = self._get("ui_language", "")
        return value if any(code == value for code, _name in UI_LANGUAGES) else ""

    @ui_language.setter
    def ui_language(self, value: str) -> None:
        self._set("ui_language", value or "")

    @property
    def reply_style(self) -> str:
        return self._choice("reply_style")

    @reply_style.setter
    def reply_style(self, value: str) -> None:
        self._set_choice("reply_style", value)

    @property
    def notify_mode(self) -> str:


        value = self._get("notify_mode", "sound")
        return value if value in ("off", "icon", "sound") else "sound"

    @notify_mode.setter
    def notify_mode(self, value: str) -> None:
        self._set("notify_mode", value if value in ("off", "icon", "sound") else "sound")

    @property
    def notify_sound_waiting(self) -> str:

        return self._get("notify_sound_waiting", "chime")

    @notify_sound_waiting.setter
    def notify_sound_waiting(self, value: str) -> None:
        self._set("notify_sound_waiting", value)

    @property
    def notify_sound_finished(self) -> str:

        return self._get("notify_sound_finished", "chime")

    @notify_sound_finished.setter
    def notify_sound_finished(self, value: str) -> None:
        self._set("notify_sound_finished", value)

    @property
    def mascot_table(self) -> str:


        return self._get("mascot_table", "")

    @mascot_table.setter
    def mascot_table(self, value: str) -> None:
        self._set("mascot_table", value)

    @property
    def layer_naming(self) -> str:
        return self._choice("layer_naming")

    @layer_naming.setter
    def layer_naming(self, value: str) -> None:
        self._set_choice("layer_naming", value)

    @property
    def send_shortcut(self) -> str:






        return self._choice("send_shortcut")

    @send_shortcut.setter
    def send_shortcut(self, value: str) -> None:
        self._set_choice("send_shortcut", value)


    @property
    def profile_name(self) -> str:

        return self._get("profile_name", "")

    @profile_name.setter
    def profile_name(self, value: str) -> None:
        self._set("profile_name", (value or "").strip()[:PROFILE_LINE_MAX_CHARS])

    @property
    def profile_role(self) -> str:

        return self._get("profile_role", "")

    @profile_role.setter
    def profile_role(self, value: str) -> None:
        self._set("profile_role", (value or "").strip()[:PROFILE_LINE_MAX_CHARS])

    @property
    def profile_prefilled(self) -> str:


        return self._get("profile_prefilled", "")

    @profile_prefilled.setter
    def profile_prefilled(self, value: str) -> None:
        self._set("profile_prefilled", value or "")

    @property
    def profile_about(self) -> str:
        return self._get("profile_about", "")

    @profile_about.setter
    def profile_about(self, value: str) -> None:
        self._set("profile_about", value or "")

    @property
    def profile_instructions(self) -> str:
        return self._get("profile_instructions", "")

    @profile_instructions.setter
    def profile_instructions(self, value: str) -> None:
        self._set("profile_instructions", value or "")

    @property
    def suggestions_enabled(self) -> bool:

        return self._get("suggestions_enabled", "1") == "1"

    @suggestions_enabled.setter
    def suggestions_enabled(self, value: bool) -> None:
        self._set("suggestions_enabled", "1" if value else "0")



    PREFERENCES = ("ui_language", "reply_style", "suggestions_enabled", "notify_mode",
                   "notify_sound_waiting", "notify_sound_finished")

    def reset_preferences(self) -> None:
        for name in self.PREFERENCES:
            self._s.remove(self._key(name))

    @property
    def memory_enabled(self) -> bool:
        return self._get("memory_enabled", "1") == "1"

    @memory_enabled.setter
    def memory_enabled(self, value: bool) -> None:
        self._set("memory_enabled", "1" if value else "0")

    @property
    def memory_notes(self) -> list:


        raw = self._get("memory_notes", "")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []

    @memory_notes.setter
    def memory_notes(self, notes: list) -> None:
        self._set("memory_notes", json.dumps(list(notes or []), ensure_ascii=False))

    @property
    def memory_forgotten(self) -> list:

        try:
            data = json.loads(self._get("memory_forgotten", "") or "[]")
        except ValueError:
            return []
        return [str(t) for t in data if isinstance(t, str)] if isinstance(data, list) else []

    @memory_forgotten.setter
    def memory_forgotten(self, texts: list) -> None:
        self._set("memory_forgotten", json.dumps(list(texts or []), ensure_ascii=False))

    @property
    def personal_owner(self) -> str:

        return self._get("personal_owner", "")

    @personal_owner.setter
    def personal_owner(self, tag: str) -> None:
        self._set("personal_owner", tag or "")

    @property
    def personal_by_account(self) -> dict:

        try:
            data = json.loads(self._get("personal_by_account", "") or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @personal_by_account.setter
    def personal_by_account(self, values: dict) -> None:
        self._set("personal_by_account", json.dumps(values or {}, ensure_ascii=False))


    @property
    def known_manifest_hash(self) -> str:


        return self._get("known_manifest_hash", "")

    @known_manifest_hash.setter
    def known_manifest_hash(self, value: str) -> None:
        self._set("known_manifest_hash", value or "")

    @property
    def saved_session(self) -> str:



        return self._get("saved_session", "")

    @saved_session.setter
    def saved_session(self, value: str) -> None:
        self._set("saved_session", value or "")

    @property
    def last_good_addresses(self) -> dict:


        try:
            held = json.loads(self._get("last_good_addresses", "") or "{}")
        except ValueError:
            return {}
        return {str(h): str(a) for h, a in held.items() if h and a} if isinstance(held, dict) else {}

    @last_good_addresses.setter
    def last_good_addresses(self, value: dict) -> None:
        self._set("last_good_addresses", json.dumps(value or {}, sort_keys=True))








    def _allow_map(self) -> dict:
        raw = self._get("allow_project", "")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict = {}
        for project, tools in data.items():
            if project == UNSAVED_KEY:



                continue
            if isinstance(tools, dict):
                out[project] = {str(name): str(when or "") for name, when in tools.items()}
            elif isinstance(tools, list):
                out[project] = {str(name): "" for name in tools}
        return out

    @staticmethod
    def _grant_key(project_path: str) -> str:






        text = str(project_path or "").strip()
        if not text or text == UNSAVED_KEY:
            return UNSAVED_KEY
        try:
            return os.path.normcase(os.path.abspath(text)).replace("\\", "/")
        except (OSError, ValueError):
            return text

    def _grant_keys(self, project_path: str) -> tuple:

        key = self._grant_key(project_path)
        raw = str(project_path or "").strip() or UNSAVED_KEY
        return (key,) if raw == key else (key, raw)





    def folder_grants(self, project_path: str) -> list:
        key = self._grant_key(project_path)
        if key == UNSAVED_KEY:
            return []
        try:
            data = json.loads(self._get("folder_grants", "") or "{}")
        except ValueError:
            return []
        rows = data.get(key) if isinstance(data, dict) else None
        return [[str(r[0]), bool(r[1])] for r in rows or ()
                if isinstance(r, (list, tuple)) and len(r) == 2 and str(r[0])]

    def set_folder_grants(self, project_path: str, grants) -> None:
        key = self._grant_key(project_path)
        if key == UNSAVED_KEY:
            return
        try:
            data = json.loads(self._get("folder_grants", "") or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        rows = [[str(f), bool(w)] for f, w in grants or ()]
        data.pop(key, None)
        if rows:
            data[key] = rows
        data = {k: v for k, v in data.items() if not _project_deleted(k)}
        self._set("folder_grants", json.dumps(dict(list(data.items())[-_GRANT_PROJECTS_MAX:])))

    def is_allowed(self, project_path: str, tool_name: str) -> bool:
        if self._grant_key(project_path) == UNSAVED_KEY:
            return False
        data = self._allow_map()
        return any(tool_name in data.get(key, {}) for key in self._grant_keys(project_path))

    def _machine_seed(self) -> bytes:



        try:
            seed = self._s.value(_DEVICE_SEED_KEY, "", type=str)
        except Exception:
            seed = ""
        if not seed:
            seed = self._inherited_seed() or uuid.uuid4().hex
            self._s.setValue(_DEVICE_SEED_KEY, seed)
        return seed.encode("utf-8")

    @staticmethod
    def _inherited_seed() -> str | None:
        try:
            raw = bytes(QSysInfo.machineUniqueId())
        except Exception:
            return None
        if not raw:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return text if text.encode("utf-8") == raw else None


    @property
    def device_hash(self) -> str:
        global _device_hash_cache
        if _device_hash_cache is None:
            _device_hash_cache = hashlib.sha256(self._machine_seed()).hexdigest()[:_DEVICE_HASH_LEN]
        return _device_hash_cache


    @staticmethod
    def _auth_manager():
        try:
            am = QgsApplication.authManager()
            return am if am is not None and am.masterPasswordIsSet() else None
        except Exception:
            return None

    @staticmethod
    def _remove_auth_config(authcfg_id: str) -> None:






        if not authcfg_id:
            return
        try:
            am = QgsApplication.authManager()
            if am is not None:
                am.removeAuthenticationConfig(authcfg_id)
        except Exception:  # nosec B110
            pass

    def _move_to_plain(self, key: str) -> bool:





        self._set(_LEGACY_KEY, key)
        try:
            self._s.sync()
            with open(self._s.fileName(), encoding="utf-8", errors="replace") as fh:
                return key in fh.read()
        except (OSError, AttributeError, TypeError):
            return False

    @property
    def activation_key(self) -> str:
        authcfg_id = self._get(_AUTHCFG_KEY, "")
        am = self._auth_manager() if authcfg_id else None
        if am is not None:
            try:
                cfg = QgsAuthMethodConfig()
                if am.loadAuthenticationConfig(authcfg_id, cfg, True):
                    key = cfg.config("password", "") or ""
                    if key:


                        if self._move_to_plain(key):
                            self._set(_AUTHCFG_KEY, "")
                            self._remove_auth_config(authcfg_id)
                        return key
            except Exception:  # nosec B110
                pass
        protected = self._get(_PROTECTED_KEY, "")
        if protected:
            key = _unprotect(protected)
            if key:
                if self._move_to_plain(key):
                    self._set(_PROTECTED_KEY, "")
                return key
        return self._get(_LEGACY_KEY, "")

    def set_activation_key(self, key: str) -> None:
        reset_account_tag_cache()
        key = (key or "").strip()
        if not key:
            self.clear_activation_key()
            return
        previous = self._get(_AUTHCFG_KEY, "")
        self._set(_LEGACY_KEY, key)
        self._set(_PROTECTED_KEY, "")
        self._set(_AUTHCFG_KEY, "")
        self._remove_auth_config(previous)

    def clear_activation_key(self) -> None:
        reset_account_tag_cache()
        self._remove_auth_config(self._get(_AUTHCFG_KEY, ""))
        self._set(_AUTHCFG_KEY, "")
        self._set(_LEGACY_KEY, "")
        self._set(_PROTECTED_KEY, "")
        self._set(_ACCOUNT_TICKET_KEY, "")

        with contextlib.suppress(Exception):
            from . import data_access

            data_access.forget()
        try:
            self._s.sync()
        except Exception:  # nosec B110
            pass

    def data_access(self) -> str:

        stored = self._get(_DATA_ACCESS_KEY, "")
        return _unprotect(stored) if stored.startswith(_DPAPI_PREFIX) else stored

    def set_data_access(self, text: str) -> None:
        self._set(_DATA_ACCESS_KEY, (_protect(text) or text) if text else "")

    def account_ticket(self) -> str:

        stored = self._get(_ACCOUNT_TICKET_KEY, "")
        return _unprotect(stored) if stored.startswith(_DPAPI_PREFIX) else stored

    def set_account_ticket(self, text: str) -> None:
        self._set(_ACCOUNT_TICKET_KEY, (_protect(text) or text) if text else "")

    @property
    def has_activation_key(self) -> bool:
        return bool(self.activation_key)

    def activation_key_is_locked(self) -> bool:

        if not self._get(_AUTHCFG_KEY, ""):
            return False
        if self._get(_LEGACY_KEY, "") or self._get(_PROTECTED_KEY, ""):
            return False
        return self._auth_manager() is None


_GRANT_PROJECTS_MAX = 200


def _project_deleted(key: str) -> bool:



    home = os.path.normcase(os.path.abspath(os.path.expanduser("~"))).replace("\\", "/").rstrip("/") + "/"
    if not key.startswith(home):
        return False
    try:
        return os.path.isdir(os.path.dirname(key)) and not os.path.exists(key)
    except (OSError, ValueError):
        return False
