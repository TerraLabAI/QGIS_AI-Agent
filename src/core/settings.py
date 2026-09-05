# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plugin settings over QgsSettings, group `TerraLab/AIAgent`."""









from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone

from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings
from qgis.PyQt.QtCore import QLocale, QSysInfo

from .host_platform import retry_file_op
from .plan import effort_allowed, effort_default, effort_name

GROUP = "TerraLab/AIAgent"
DEFAULT_SERVER_URL = "wss://agent.terra-lab.ai/ws"
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")








UNSAVED_KEY = "(unsaved)"
_unsaved_grants: dict[str, str] = {}
_unsaved_watched = False


def forget_unsaved_grants() -> None:
    """A new project is a new project: nothing carries over to it."""
    _unsaved_grants.clear()


def _watch_project_cleared() -> None:
    """Connect ``QgsProject.cleared`` once, so New Project drops the grants."""
    global _unsaved_watched
    if _unsaved_watched:
        return
    _unsaved_watched = True
    try:
        from qgis.core import QgsProject

        QgsProject.instance().cleared.connect(forget_unsaved_grants)
    except Exception:  # nosec B110 - without the signal the grants last the session
        pass


def server_url_problem(url: str) -> str | None:
    """None when ``url`` may carry the activation key, else why not."""





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


DEFAULT_WEBSITE_URL = "https://terra-lab.ai"
PRODUCT_ID = "ai-agent"
KEY_RE = re.compile(r"^tl_[0-9a-f]{32}$")



PROFILE_LINE_MAX_CHARS = 80

_AUTHCFG_KEY = "authcfg_id"
_LEGACY_KEY = "activation_key"
_DEVICE_SEED_KEY = "TerraLab/device_seed"
_DEVICE_HASH_LEN = 16

_device_hash_cache: str | None = None


def state_dir() -> str:
    """Per-machine state folder: caches and the local data index."""



    base = os.environ.get("AI_AGENT_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".qgis_ai-agent-product")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return base














_ACCOUNT_DIR_LEN = 16
_MOVED_ON_UPGRADE = ("threads", "snapshots", "traces", "idempotency.json")
_account_tag_cache: str | None = None


def reset_account_tag_cache() -> None:
    """Forget which account we are."""








    global _account_tag_cache
    _account_tag_cache = None
    try:
        from . import net

        net.clear_cache()
    except Exception:  # nosec B110 - noqa: BLE001 - signing out never fails on tidying
        pass


def account_tag(key: str = "") -> str:
    """A stable folder name for the signed-in account, or "signed-out"."""
    global _account_tag_cache
    if key:
        return "a" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:_ACCOUNT_DIR_LEN]
    if _account_tag_cache is None:
        try:
            current = Settings().activation_key
        except Exception:  # noqa: BLE001 - no QGIS settings backend yet
            current = ""
        _account_tag_cache = (
            "a" + hashlib.sha256(current.encode("utf-8")).hexdigest()[:_ACCOUNT_DIR_LEN]
            if current else "signed-out"
        )
    return _account_tag_cache


def account_dir() -> str:
    """The state folder of the account signed in now. Everything private lives here."""
    folder = os.path.join(state_dir(), "accounts", account_tag())
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return state_dir()
    _adopt_legacy_state(folder)
    return folder


def _adopt_legacy_state(folder: str) -> None:
    """Move the pre-account-folder state in, once, for whoever is signed in first."""






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
    """The only form of the key that may ever be logged or shown."""
    return (key or "")[:6]


class Settings:
    def __init__(self, settings=None):
        self._s = settings or QgsSettings()
        self._session_approval = ""



    @staticmethod
    def _key(name: str) -> str:
        return f"{GROUP}/{name}"

    def _get(self, name: str, default: str = "") -> str:
        try:
            value = self._s.value(self._key(name), default, type=str)
        except Exception:  # nosec B110 - legacy setting fallback
            value = self._s.value(self._key(name), default)
        return value if isinstance(value, str) else (default if value is None else str(value))

    def _set(self, name: str, value) -> None:
        self._s.setValue(self._key(name), value)

    @property
    def last_run_version(self) -> str:
        """The plugin version that ran last in this profile; empty on a first load."""
        return self._get("last_run_version", "")

    @last_run_version.setter
    def last_run_version(self, value: str) -> None:
        self._set("last_run_version", str(value or ""))



    @property
    def dock_visible(self) -> bool:
        return self._get("dock_visible", "1") == "1"

    @dock_visible.setter
    def dock_visible(self, value: bool) -> None:
        self._set("dock_visible", "1" if value else "0")

    @property
    def onboarded(self) -> bool:
        """True once a run finished or the user closed the welcome card."""
        return self._get("onboarded", "0") == "1"

    @onboarded.setter
    def onboarded(self, value: bool) -> None:
        self._set("onboarded", "1" if value else "0")



    @property
    def server_url(self) -> str:
        """Where the panel connects: the stored value, or the shipped default."""





        stored = self._get("server_url", DEFAULT_SERVER_URL) or DEFAULT_SERVER_URL
        return stored if server_url_problem(stored) is None else DEFAULT_SERVER_URL

    @server_url.setter
    def server_url(self, value: str) -> None:
        self._set("server_url", (value or "").strip() or DEFAULT_SERVER_URL)

    @property
    def website_url(self) -> str:
        env = os.environ.get("TERRALAB_BASE_URL")
        if env:
            return env.rstrip("/")
        return (self._get("website_url", DEFAULT_WEBSITE_URL) or DEFAULT_WEBSITE_URL).rstrip("/")

    @website_url.setter
    def website_url(self, value: str) -> None:
        self._set("website_url", (value or "").strip().rstrip("/") or DEFAULT_WEBSITE_URL)






















    @property
    def known_connectors(self) -> list:
        """The last connector list the server sent, as a JSON list of dicts."""






        raw = self._get("known_connectors", "")
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
        self._set("known_connectors", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def known_qgis_plugins(self) -> list:
        """The last thing the server said about QGIS plugins, cached the same way."""





        raw = self._get("known_qgis_plugins", "")
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
        self._set("known_qgis_plugins", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def known_basemaps(self) -> list:
        """The basemap presets the server last sent, cached like the connectors."""




        raw = self._get("known_basemaps", "")
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
        self._set("known_basemaps", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def known_use_cases(self) -> list:
        """The Examples library the server last sent, cached like the basemaps."""






        raw = self._get("known_use_cases", "")
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
        self._set("known_use_cases", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def known_use_case_groups(self) -> list:
        """The Examples rail's groups the server last sent, beside the rows above."""
        raw = self._get("known_use_case_groups", "")
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
        self._set("known_use_case_groups", json.dumps(clean, ensure_ascii=False) if clean else "")

    @property
    def hidden_plugins(self) -> list:
        """Plugin folders the Connectors directory leaves out, as the server named them."""





        raw = self._get("hidden_plugins", "")
        return [part for part in (piece.strip() for piece in raw.split(",")) if part]

    @hidden_plugins.setter
    def hidden_plugins(self, folders) -> None:
        clean = sorted({str(f).strip() for f in (folders or []) if str(f or "").strip()})
        self._set("hidden_plugins", ",".join(clean))

    @property
    def known_tool_names(self) -> list:
        """The tool catalog's names, so Settings can count them before the dock opens."""
        raw = self._get("known_tool_names", "")
        return [part for part in (piece.strip() for piece in raw.split(",")) if part]

    @known_tool_names.setter
    def known_tool_names(self, names) -> None:
        clean = sorted({str(n).strip() for n in (names or []) if str(n or "").strip()})
        self._set("known_tool_names", ",".join(clean))



    @property
    def mode(self) -> str:
        value = self._get("mode", "agent")
        return value if value in ("ask", "agent") else "agent"

    @mode.setter
    def mode(self, value: str) -> None:
        self._set("mode", value if value in ("ask", "agent") else "agent")

    EFFORTS = ("low", "medium", "high")

    @property
    def effort(self) -> str:
        """``low``, ``medium`` or ``high``: how hard the agent works on a message, the composer's slider."""





















        value = self._get("effort", "")





        known = effort_name(value)
        if known:
            return known
        legacy = {"fast": "low", "pro": "medium"}.get(self._get("model_mode", ""), "")
        if legacy and effort_allowed(legacy):
            return legacy
        return effort_default()

    @effort.setter
    def effort(self, value: str) -> None:
        self._set("effort", value if value in self.EFFORTS else "low")

    @property
    def approval(self) -> str:
        """``careful``, ``ask`` or ``auto``."""

        if self._session_approval:
            return self._session_approval
        value = self._get("approval", "ask")
        return value if value in ("careful", "ask") else "ask"

    @approval.setter
    def approval(self, value: str) -> None:
        if value == "auto":
            self._session_approval = "auto"
            return
        self._session_approval = ""
        self._set("approval", value if value in ("careful", "ask") else "ask")

    def reset_autopilot(self) -> bool:
        """Back to Balanced when Autopilot was on; True when something changed."""
        if self._session_approval != "auto":
            return False
        self._session_approval = ""
        return True

    @property
    def locale(self) -> str:
        stored = self._get("locale", "")
        if stored:
            return stored
        try:
            override = self._s.value("locale/overrideFlag", False, type=bool)
            user = self._s.value("locale/userLocale", "", type=str)
            if override and user:


                return user[:5]
        except Exception:  # nosec B110 - legacy setting fallback
            pass
        return (QLocale.system().name() or "en")[:5]

    @locale.setter
    def locale(self, value: str) -> None:
        self._set("locale", (value or "")[:5])



    @property
    def reply_language(self) -> str:
        """Locale code the answers are written in; "" follows the QGIS locale."""
        return self._get("reply_language", "")[:5]

    @reply_language.setter
    def reply_language(self, value: str) -> None:
        self._set("reply_language", (value or "")[:5])

    @property
    def reply_style(self) -> str:
        value = self._get("reply_style", "concise")
        return value if value in ("concise", "balanced", "detailed") else "concise"

    @reply_style.setter
    def reply_style(self, value: str) -> None:
        self._set("reply_style", value if value in ("concise", "balanced", "detailed") else "concise")

    @property
    def explain_runs(self) -> bool:
        return self._get("explain_runs", "1") == "1"

    @explain_runs.setter
    def explain_runs(self, value: bool) -> None:
        self._set("explain_runs", "1" if value else "0")

    @property
    def follow_edits(self) -> bool:
        """Whether the canvas goes to what a run edits."""






        return self._get("follow_edits", "1") == "1"

    @follow_edits.setter
    def follow_edits(self, value: bool) -> None:
        self._set("follow_edits", "1" if value else "0")

    @property
    def show_tool_details(self) -> bool:
        return self._get("show_tool_details", "1") == "1"

    @show_tool_details.setter
    def show_tool_details(self, value: bool) -> None:
        self._set("show_tool_details", "1" if value else "0")

    QUESTION_TIMEOUTS = (0, 30, 60, 120)

    @property
    def question_timeout_s(self) -> int:
        """Seconds an unanswered question waits before the card takes the recommended option (the first one without a recommendation)."""

        try:
            value = int(self._get("question_timeout_s", "60") or 60)
        except ValueError:
            return 60
        return max(0, value)

    @question_timeout_s.setter
    def question_timeout_s(self, value: int) -> None:
        try:
            seconds = max(0, int(value))
        except (TypeError, ValueError):
            seconds = 60
        self._set("question_timeout_s", str(seconds))



    @property
    def question_policy(self) -> str:
        value = self._get("question_policy", "balanced")
        return value if value in ("balanced", "minimal", "confirm") else "balanced"

    @question_policy.setter
    def question_policy(self, value: str) -> None:
        self._set("question_policy", value if value in ("balanced", "minimal", "confirm") else "balanced")

    @property
    def expertise(self) -> str:
        value = self._get("expertise", "")
        return value if value in ("beginner", "intermediate", "expert") else ""

    @expertise.setter
    def expertise(self, value: str) -> None:
        self._set("expertise", value if value in ("beginner", "intermediate", "expert") else "")

    @property
    def units(self) -> str:
        value = self._get("units", "metric")
        return value if value in ("metric", "imperial") else "metric"

    @units.setter
    def units(self, value: str) -> None:
        self._set("units", value if value in ("metric", "imperial") else "metric")

    @property
    def layer_naming(self) -> str:
        value = self._get("layer_naming", "human")
        return value if value in ("human", "snake_case") else "human"

    @layer_naming.setter
    def layer_naming(self, value: str) -> None:
        self._set("layer_naming", value if value in ("human", "snake_case") else "human")

    @property
    def send_shortcut(self) -> str:
        """Which key sends the message: "enter" or "modifier"."""








        value = self._get("send_shortcut", "enter")
        return value if value in ("enter", "modifier") else "enter"

    @send_shortcut.setter
    def send_shortcut(self, value: str) -> None:
        self._set("send_shortcut", value if value in ("enter", "modifier") else "enter")



    @property
    def profile_name(self) -> str:
        """What the agent should call the user. Empty means it uses no name."""
        return self._get("profile_name", "")

    @profile_name.setter
    def profile_name(self, value: str) -> None:
        self._set("profile_name", (value or "").strip()[:PROFILE_LINE_MAX_CHARS])

    @property
    def profile_role(self) -> str:
        """The user's job in one line: "urban planner", "hydrologist", "student"."""
        return self._get("profile_role", "")

    @profile_role.setter
    def profile_role(self, value: str) -> None:
        self._set("profile_role", (value or "").strip()[:PROFILE_LINE_MAX_CHARS])

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
    def memory_enabled(self) -> bool:
        return self._get("memory_enabled", "1") == "1"

    @memory_enabled.setter
    def memory_enabled(self, value: bool) -> None:
        self._set("memory_enabled", "1" if value else "0")

    @property
    def memory_notes(self) -> list:
        """The notes as stored: a JSON list of ``{text, created_at, source}``."""

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





    PREFERENCE_KEYS = (
        "mode", "approval", "effort", "model_mode", "reply_language", "reply_style", "explain_runs",
        "show_tool_details",
        "question_timeout_s", "question_policy", "follow_edits", "send_shortcut",
        "expertise", "units", "layer_naming",
        "profile_name", "profile_role", "profile_about", "profile_instructions", "memory_enabled",
        "memory_notes", "allow_project",




        "disabled_connectors", "enabled_connectors", "disabled_plugins",
    )

    def reset_preferences(self) -> None:
        """Back to the defaults for everything the settings dialog edits."""

        for name in self.PREFERENCE_KEYS:
            try:
                self._s.remove(self._key(name))
            except Exception:  # nosec B110 - a missing key is the goal
                self._set(name, "")
        try:
            self._s.sync()
        except Exception:  # nosec B110 - best effort
            pass



    @property
    def known_manifest_hash(self) -> str:
        return self._get("known_manifest_hash", "")

    @known_manifest_hash.setter
    def known_manifest_hash(self, value: str) -> None:
        self._set("known_manifest_hash", value or "")




















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

    def grants(self) -> dict:
        """Every "allow for this project" grant: {project: {tool: granted at}}."""





        data = self._allow_map()
        if _unsaved_grants:
            data[UNSAVED_KEY] = dict(_unsaved_grants)
        return data

    @staticmethod
    def _grant_key(project_path: str) -> str:
        """One spelling of a project path, so a grant survives reopening it."""








        text = str(project_path or "").strip()
        if not text or text == UNSAVED_KEY:
            return UNSAVED_KEY
        try:
            return os.path.normcase(os.path.abspath(text)).replace("\\", "/")
        except (OSError, ValueError):
            return text

    def _grant_keys(self, project_path: str) -> tuple:
        """The key to write, then the raw one, for grants stored before it."""
        key = self._grant_key(project_path)
        raw = str(project_path or "").strip() or UNSAVED_KEY
        return (key,) if raw == key else (key, raw)

    def is_allowed(self, project_path: str, tool_name: str) -> bool:
        if self._grant_key(project_path) == UNSAVED_KEY:
            return tool_name in _unsaved_grants
        data = self._allow_map()
        return any(tool_name in data.get(key, {}) for key in self._grant_keys(project_path))

    def allow(self, project_path: str, tool_name: str) -> None:
        if self._grant_key(project_path) == UNSAVED_KEY:

            _watch_project_cleared()
            _unsaved_grants.setdefault(
                tool_name, datetime.now(timezone.utc).replace(microsecond=0).isoformat())
            return
        data = self._allow_map()
        tools = data.setdefault(self._grant_key(project_path), {})
        tools.setdefault(tool_name, datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        self._set("allow_project", json.dumps(data))

    def revoke(self, project_path: str, tool_name: str) -> bool:
        """Drop one grant. True when there was one to drop."""
        if self._grant_key(project_path) == UNSAVED_KEY:
            return _unsaved_grants.pop(tool_name, None) is not None
        data = self._allow_map()
        dropped = False
        for key in self._grant_keys(project_path):
            tools = data.get(key)
            if not isinstance(tools, dict) or tool_name not in tools:
                continue
            tools.pop(tool_name, None)
            dropped = True
            if not tools:
                data.pop(key, None)
        if not dropped:
            return False
        self._set("allow_project", json.dumps(data))
        return True

    def clear_allowed(self, project_path: str | None = None) -> None:
        if project_path is None:
            forget_unsaved_grants()
            self._set("allow_project", "")
            return
        if self._grant_key(project_path) == UNSAVED_KEY:
            forget_unsaved_grants()
            return
        data = self._allow_map()
        for key in self._grant_keys(project_path):
            data.pop(key, None)
        self._set("allow_project", json.dumps(data))



    def _machine_seed(self) -> bytes:
        """The per-machine seed under the shared `TerraLab/device_seed` key."""


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

    @property
    def activation_key(self) -> str:
        authcfg_id = self._get(_AUTHCFG_KEY, "")
        if authcfg_id:
            am = self._auth_manager()
            if am is not None:
                try:
                    cfg = QgsAuthMethodConfig()
                    if am.loadAuthenticationConfig(authcfg_id, cfg, True):
                        key = cfg.config("password", "") or ""
                        if key:
                            return key
                except Exception:  # nosec B110 - legacy setting fallback
                    pass
        return self._get(_LEGACY_KEY, "")

    def set_activation_key(self, key: str) -> None:
        reset_account_tag_cache()
        key = (key or "").strip()
        if not key:
            self.clear_activation_key()
            return
        am = self._auth_manager()
        if am is not None:
            try:
                cfg = QgsAuthMethodConfig()
                cfg.setName("AI Agent activation key")
                cfg.setMethod("Basic")
                cfg.setConfig("password", key)
                if am.storeAuthenticationConfig(cfg) and cfg.id():
                    self._set(_AUTHCFG_KEY, cfg.id())
                    self._set(_LEGACY_KEY, "")
                    return
            except Exception:  # nosec B110 - legacy setting fallback
                pass
        self._set(_LEGACY_KEY, key)
        self._set(_AUTHCFG_KEY, "")

    def clear_activation_key(self) -> None:
        reset_account_tag_cache()
        authcfg_id = self._get(_AUTHCFG_KEY, "")
        if authcfg_id:
            am = self._auth_manager()
            if am is not None:
                try:
                    am.removeAuthenticationConfig(authcfg_id)
                except Exception:  # nosec B110 - legacy setting fallback
                    pass
        self._set(_AUTHCFG_KEY, "")
        self._set(_LEGACY_KEY, "")
        try:
            self._s.sync()
        except Exception:  # nosec B110 - legacy setting fallback
            pass

    @property
    def has_activation_key(self) -> bool:
        return bool(self.activation_key)

    def activation_key_is_locked(self) -> bool:
        """A key sits in the QGIS auth database but the master password is not typed yet."""
        if not self._get(_AUTHCFG_KEY, ""):
            return False
        if self._get(_LEGACY_KEY, ""):
            return False
        return self._auth_manager() is None
