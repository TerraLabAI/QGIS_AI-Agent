# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI Edit debugging adapter."""












from __future__ import annotations

import importlib
import os
import re
from typing import Any

from ...core import net
from ...core.security import validate_path
from .._widgets import AI_EDIT_KEYS, process_events
from .ai_edit_access import ACCESS as _ACCESS
from .base import PluginAdapter

_KEY_RE = re.compile(r"^tl_[0-9a-f]{32}$")
_TEST_EMAIL_DOMAINS = ("@terra-lab.ai",)




_ACTIVATE_MAX_BYTES = 64 * 1024



_AK = "activation" + "_key"
_HAS_KEY = "has_" + _AK
_SET_KEY = "set_" + _AK


def _has_key(auth) -> bool:
    fn = getattr(auth, _HAS_KEY, None) if auth is not None else None
    try:
        return bool(fn()) if callable(fn) else False
    except Exception:
        return False


def _set_key(auth, value: str) -> bool:
    fn = getattr(auth, _SET_KEY, None) if auth is not None else None
    if not callable(fn):
        return False
    try:
        fn(value)
        return True
    except Exception:
        return False


def _attr(inst, *names):
    """First existing attribute among names (handles private/public variants)."""
    for name in names:
        if hasattr(inst, name):
            return getattr(inst, name)
    return None


def _module_func(inst, name):
    """Resolve a function imported into the live plugin module's namespace."""
    try:
        mod = importlib.import_module(type(inst).__module__)
        return getattr(mod, name, None)
    except Exception:
        return None


_SECRETISH_KEY_RE = re.compile(r"key|token|secret", re.IGNORECASE)


def _usage_fields(raw: dict) -> dict:
    """Pass the backend usage dict through whole, old names kept as aliases."""










    if not isinstance(raw, dict):
        return {}
    out = {k: v for k, v in raw.items() if not _SECRETISH_KEY_RE.search(str(k))}
    dropped = sorted(str(k) for k in raw if _SECRETISH_KEY_RE.search(str(k)))
    if dropped:
        out["_dropped_credential_fields"] = dropped
    out["used"] = raw.get("images_used")
    out["limit"] = raw.get("images_limit")
    out["is_free"] = raw.get("is_free_tier")
    return out


class AiEditAdapter(PluginAdapter):
    keys = AI_EDIT_KEYS
    aliases = ("ai-edit", "aiedit", "ai edit")

    def __init__(self):
        self._usage_patch_backup: dict[str, Any] | None = None

    def display_name(self) -> str:
        return "AI Edit"

    def log_tags(self) -> tuple[str, ...]:
        return ("AI Edit", "AIEdit")



    def _inst(self):
        _, inst = self.find_instance()
        return inst

    def _auth(self, inst):
        return _attr(inst, "_auth_manager", "auth_manager")

    def _client(self, inst):
        return _attr(inst, "_client", "client")

    def _dock(self, inst):
        return _attr(inst, "_dock_widget", "dock_widget")



    def state(self) -> dict:
        inst = self._inst()
        if inst is None:
            return {"available": False, "plugin": self.display_name()}
        auth = self._auth(inst)
        dock = self._dock(inst)
        signed_in = _has_key(auth)

        out: dict[str, Any] = {
            "available": True,
            "plugin": "AI Edit",
            "signed_in": signed_in,
            "key_present": signed_in,
            "usage_mocked": self._usage_patch_backup is not None,
            "dev_flags": {
                "debug": bool(_attr(inst, "_dev_mode")),
                "skip_trial": bool(_attr(inst, "_skip_trial_check")),
            },
        }

        if auth and hasattr(auth, "get_usage_info"):
            try:
                out["usage"] = _usage_fields(auth.get_usage_info())
            except Exception as err:
                out["usage"] = {"error": str(err)}

        if dock is not None:
            try:




                out["dock"] = {
                    "visible": dock.isVisible(),
                    "activated": bool(getattr(dock, "_activated", None)),
                    "is_free_tier": bool(getattr(dock, "_is_free_tier", None)),
                    "model_picker": False,
                    "selected_resolution": (
                        dock.get_selected_resolution()
                        if hasattr(dock, "get_selected_resolution") else None
                    ),
                }
            except Exception:  # nosec B110 - AI Edit state is optional
                pass

        extent = _attr(inst, "_selected_extent")
        if extent is not None:
            try:
                out["selected_extent"] = {
                    "xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
                    "xmax": extent.xMaximum(), "ymax": extent.yMaximum(),
                }
            except Exception:  # nosec B110 - AI Edit state is optional
                pass
        return out



    def flows(self) -> dict[str, str]:
        return {
            "signout": "Sign out locally (clear key + show activation screen).",
            "set_key": "Inject a test license key. params: key, validate_with_server?",
            "mock_usage": "Mock credits in-memory. params: used, limit, is_free.",
            "restore_usage": "Undo mock_usage.",
            "set_dev_flags": "Write DEBUG/SKIP_TRIAL_CHECK to AI Edit .env.local. params: debug?, skip_trial?",
            "simulate_signup": "Sign out and show the activation/signup screen.",
            "run_generation": (
                "Run a generation. params: prompt, bbox?[xmin,ymin,xmax,ymax]|use_canvas_extent?, resolution?"
            ),
            "generation_status": "Report the latest generation status + new layers.",
            "attach_reference": (
                "Attach a reference image/layer to the dock. params: path?|layer_name?, use_zone_extent?"
            ),
            "set_resolution": "Set output resolution. params: resolution (1K/2K/4K).",
            "set_model": "No-op: AI Edit has no model selector in this version (resolution only).",
            "clear_references": "Remove all attached references.",
            "new_free_key": "GATED Tier-2: create a free key via the website. params: email, confirm.",
        }

    def reset(self, scenario: str | None) -> dict:
        scenario = scenario or "signout"
        if scenario in ("signout", "signout_keep_consent"):
            return self.do_signout()
        if scenario == "factory":
            res = self.do_signout()
            self._reset_device_seed()
            self.do_restore_usage()
            return {"factory_reset": True, "signout": res}
        return {"_error": f"Unknown reset scenario '{scenario}'", "scenarios": ["signout", "factory"]}

    def seed(self, scenario: str | None) -> dict:
        if scenario == "activation_screen":
            return self.do_simulate_signup()
        return {"_error": f"Unknown seed scenario '{scenario}'", "scenarios": ["activation_screen"]}

    def run_flow(self, name: str, params: dict) -> dict:
        params = params or {}
        dispatch = {
            "signout": lambda: self.do_signout(),
            "set_key": lambda: self.do_set_key(params.get("key", ""), params.get("validate_with_server", False)),
            "mock_usage": lambda: self.do_mock_usage(
                params.get("used"), params.get("limit"), params.get("is_free", True)
            ),
            "restore_usage": lambda: self.do_restore_usage(),
            "set_dev_flags": lambda: self.do_set_dev_flags(params.get("debug"), params.get("skip_trial")),
            "set_model": lambda: self.do_set_model(params.get("model_id", "")),
            "simulate_signup": lambda: self.do_simulate_signup(),
            "run_generation": lambda: self.do_run_generation(params),
            "generation_status": lambda: self.do_generation_status(),
            "attach_reference": lambda: self.do_attach_reference(
                params.get("path", ""), params.get("layer_name", ""), params.get("use_zone_extent", True)
            ),
            "set_resolution": lambda: self.do_set_resolution(params.get("resolution", "")),
            "clear_references": lambda: self.do_clear_references(),
            "new_free_key": lambda: self.do_new_free_key(params.get("email", ""), params.get("confirm", False)),
        }
        fn = dispatch.get(name)
        if fn is None:
            return {"_error": f"Unknown flow '{name}'", "available_flows": self.flows()}
        return fn()



    def do_signout(self) -> dict:
        inst = self._inst()
        if inst is None:
            return {"_error": "AI Edit not loaded"}
        auth = self._auth(inst)
        dock = self._dock(inst)
        _set_key(auth, "")
        clear_fn = _module_func(inst, "clear_activation")
        cleared = False
        if callable(clear_fn):
            try:
                clear_fn()
                cleared = True
            except Exception as err:
                return {"_error": f"clear_activation failed: {err}"}
        if dock is not None and hasattr(dock, "set_activated"):
            try:
                dock.set_activated(False)
            except Exception:  # nosec B110 - AI Edit state is optional
                pass
        process_events()
        return {"ok": True, "signed_in": False, "store_cleared": cleared}

    def do_set_key(self, key: str, validate_with_server: bool = False) -> dict:
        inst = self._inst()
        if inst is None:
            return {"_error": "AI Edit not loaded"}
        key = (key or "").strip()
        if not _KEY_RE.match(key):
            return {"ok": False, "format_valid": False, "message": "Key must match tl_<32 hex>."}

        auth = self._auth(inst)
        dock = self._dock(inst)

        if validate_with_server:
            attempt = _attr(inst, "_on_activation_attempted")
            if callable(attempt):
                try:
                    attempt(key)
                    process_events()
                    signed = _has_key(auth)
                    return {"ok": signed, "format_valid": True, "validated": True, "signed_in": signed}
                except Exception as err:
                    return {"_error": f"Validation path failed: {err}"}


        save_fn = _module_func(inst, "save_activation")
        if callable(save_fn):
            try:
                save_fn(key)
            except Exception as err:
                return {"_error": f"save_activation failed: {err}"}
        _set_key(auth, key)
        if dock is not None and hasattr(dock, "set_activated"):
            dock.set_activated(True)
        process_events()
        signed = _has_key(auth)
        return {"ok": signed, "format_valid": True, "validated": False, "signed_in": signed}

    def do_mock_usage(self, used, limit, is_free=True) -> dict:
        inst = self._inst()
        if inst is None:
            return {"_error": "AI Edit not loaded"}
        if used is None or limit is None:
            return {"_error": "used and limit are required"}
        used, limit, is_free = int(used), int(limit), bool(is_free)
        auth = self._auth(inst)
        client = self._client(inst)
        mock = {"images_used": used, "images_limit": limit, "is_free_tier": is_free}

        if self._usage_patch_backup is None:


            real = {}
            if auth is not None:
                try:
                    real = _usage_fields(auth.get_usage_info())
                except Exception:
                    real = {}
            self._usage_patch_backup = {
                "auth_get_usage_info": getattr(auth, "get_usage_info", None) if auth else None,
                "client_get_usage": getattr(client, "get_usage", None) if client else None,
                "real_usage": real,
            }
        if auth is not None:
            auth.get_usage_info = lambda: dict(mock)
        if client is not None and hasattr(client, "get_usage"):
            client.get_usage = lambda *a, **k: dict(mock)

        dock = self._dock(inst)
        if dock is not None and hasattr(dock, "set_credits"):
            try:
                dock.set_credits(used, limit, is_free)
            except Exception:  # nosec B110 - AI Edit state is optional
                pass
        process_events()
        return {
            "ok": True,
            "usage": {"used": used, "limit": limit, "is_free": is_free},
            "paywall_expected": bool(is_free and used >= limit),
            "note": "In-memory only; lasts until restore_usage or AI Edit reload.",
        }

    def do_restore_usage(self) -> dict:
        inst = self._inst()
        if inst is None or self._usage_patch_backup is None:
            return {"ok": True, "restored": False}
        auth = self._auth(inst)
        client = self._client(inst)
        backup = self._usage_patch_backup
        try:
            if auth is not None and backup.get("auth_get_usage_info") is not None:
                auth.get_usage_info = backup["auth_get_usage_info"]
            if client is not None and backup.get("client_get_usage") is not None:
                client.get_usage = backup["client_get_usage"]
        finally:
            self._usage_patch_backup = None


        real = backup.get("real_usage") or {}
        dock = self._dock(inst)
        if (
            dock is not None and hasattr(dock, "set_credits")
            and real.get("used") is not None and real.get("limit") is not None
        ):
            try:
                dock.set_credits(real["used"], real["limit"], bool(real.get("is_free")))
            except Exception:  # nosec B110 - AI Edit state is optional
                pass
        process_events()
        return {"ok": True, "restored": True}

    def do_set_dev_flags(self, debug, skip_trial) -> dict:
        inst = self._inst()
        if inst is None:
            return {"_error": "AI Edit not loaded"}
        try:
            plugin_file = importlib.import_module(type(inst).__module__).__file__
        except Exception as err:
            return {"_error": f"Cannot locate AI Edit dir: {err}"}

        root = os.path.dirname(os.path.dirname(os.path.dirname(plugin_file)))
        env_path = os.path.join(root, ".env.local")

        values: dict[str, str] = {}
        if os.path.isfile(env_path):
            try:
                with open(env_path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        values[k.strip()] = v.strip()
            except Exception:  # nosec B110 - AI Edit state is optional
                pass
        if debug is not None:
            values["DEBUG"] = "true" if debug else "false"
            if hasattr(inst, "_dev_mode"):
                inst._dev_mode = bool(debug)
        if skip_trial is not None:
            values["SKIP_TRIAL_CHECK"] = "true" if skip_trial else "false"
            if hasattr(inst, "_skip_trial_check"):
                inst._skip_trial_check = bool(skip_trial)
        try:
            with open(env_path, "w", encoding="utf-8") as fh:
                for k, v in values.items():
                    fh.write(f"{k}={v}\n")
        except Exception as err:
            return {"_error": f"Cannot write {env_path}: {err}"}
        return {
            "ok": True,
            "dev_flags": {
                "debug": bool(_attr(inst, "_dev_mode")),
                "skip_trial": bool(_attr(inst, "_skip_trial_check")),
            },
            "note": "Reload AI Edit (reload_plugin) so _create_client re-reads the flags.",
        }

    def do_simulate_signup(self) -> dict:
        res = self.do_signout()
        inst = self._inst()
        dock = self._dock(inst) if inst else None
        if dock is not None:
            try:
                dock.show()
                dock.raise_()
                if hasattr(dock, "set_activated"):
                    dock.set_activated(False)
            except Exception:  # nosec B110 - AI Edit state is optional
                pass
        process_events()
        return {"ok": True, "dock_view": "activation", "signout": res}

    def do_run_generation(self, params: dict) -> dict:



        return _ACCESS.run_generation(params or {})

    def do_generation_status(self) -> dict:


        return _ACCESS.generation_status()

    def do_attach_reference(self, path: str = "", layer_name: str = "", use_zone_extent: bool = True) -> dict:
        """Attach a reference image (file path) or a project layer to the dock without the native file picker, so the reference flow is drivable."""





        inst = self._inst()
        if inst is None:
            return {"_error": "AI Edit not loaded"}
        dock = self._dock(inst)
        ref = getattr(dock, "_reference_widget", None) if dock is not None else None
        if ref is None:
            return {"_error": "Reference widget unavailable. Draw a zone first (launch AI Edit)."}

        path = (path or "").strip()
        layer_name = (layer_name or "").strip()
        if not path and not layer_name:
            return {"_error": "Provide 'path' (a file) or 'layer_name' (a project layer)."}

        before = ref.count() if hasattr(ref, "count") else None

        if use_zone_extent:
            extent = _attr(inst, "_selected_extent")
            crs = None
            try:
                from qgis.utils import iface
                canvas = iface.mapCanvas() if iface is not None else None
                if canvas is not None:
                    crs = canvas.mapSettings().destinationCrs()
            except Exception:
                crs = None
            if extent is not None and crs is not None and hasattr(ref, "set_target_extent"):
                try:
                    ref.set_target_extent(extent, crs)
                except Exception:  # nosec B110 - AI Edit state is optional
                    pass

        try:
            if path:
                path_error = validate_path(path, write=False)
                if path_error:
                    return {"_error": path_error}
                if not os.path.isfile(path):
                    return {"_error": f"File not found: {path}"}
                ref.add_paths([path])
                source = os.path.basename(path)
            else:
                from qgis.core import QgsProject
                matches = QgsProject.instance().mapLayersByName(layer_name)
                if not matches:
                    return {"_error": f"No project layer named '{layer_name}'."}
                ref.add_layers([matches[0]])
                source = layer_name
        except Exception as err:
            return {"_error": f"attach failed: {err}"}

        process_events()
        after = ref.count() if hasattr(ref, "count") else None
        added = bool(after is not None and before is not None and after > before)
        at_cap = ref.at_capacity() if hasattr(ref, "at_capacity") else None
        return {
            "ok": added,
            "source": source,
            "count_before": before,
            "count_after": after,
            "added": added,
            "at_capacity": at_cap,
            "note": None if added else "Reference not added (free-tier reference limit reached; upgrade for more).",
        }

    def do_set_resolution(self, resolution: str) -> dict:
        """Set the output resolution via the dock's own handler."""


        inst = self._inst()
        if inst is None:
            return {"_error": "AI Edit not loaded"}
        res = (resolution or "").strip().upper()


        valid = {"1K", "2K", "4K"}
        if res not in valid:
            return {"_error": f"resolution must be one of {sorted(valid)}"}
        dock = self._dock(inst)
        fn = getattr(dock, "_on_resolution_selected", None) if dock is not None else None
        if not callable(fn):
            return {"_error": "Resolution handler not available on the dock."}
        try:
            fn(res)
        except Exception as err:
            return {"_error": f"set resolution failed: {err}"}
        process_events()
        current = None
        getter = getattr(dock, "get_selected_resolution", None)
        if callable(getter):
            try:
                current = getter()
            except Exception:
                current = None
        return {"ok": current == res or current is None, "resolution": current or res}

    def do_set_model(self, model_id: str) -> dict:
        """AI Edit has no model selector in this version, there is no _available_models / _selected_model / _on_model_selected on the dock."""



        return {
            "_error": (
                "AI Edit has no model selector in this version; only resolution "
                "(ai_edit_set_resolution) is selectable."
            ),
            "model_picker": False,
        }

    def do_clear_references(self) -> dict:
        """Remove all attached references from the dock (free tier caps at one, and references persist across Exit/Launch, so clear between demos)."""

        inst = self._inst()
        if inst is None:
            return {"_error": "AI Edit not loaded"}
        dock = self._dock(inst)
        ref = getattr(dock, "_reference_widget", None) if dock is not None else None
        if ref is None or not hasattr(ref, "clear"):
            return {"_error": "Reference widget unavailable."}
        try:
            ref.clear()
        except Exception as err:
            return {"_error": f"clear failed: {err}"}
        process_events()
        return {"ok": True, "count": ref.count() if hasattr(ref, "count") else None}



    def _backend_allowed(self, email: str, confirm: bool) -> str | None:
        if os.environ.get("AI_AGENT_ALLOW_BACKEND_WRITES") != "1":
            return "Backend disabled: set AI_AGENT_ALLOW_BACKEND_WRITES=1 to enable."
        if not confirm:
            return "Backend call requires confirm:true."
        allow = os.environ.get("AI_AGENT_TEST_EMAIL", "").strip().lower()
        em = (email or "").strip().lower()
        if not em:
            return "email is required."
        if em == allow or any(em.endswith(d) for d in _TEST_EMAIL_DOMAINS):
            return None
        return "email is not an allowlisted test address (set AI_AGENT_TEST_EMAIL or use a @terra-lab.ai address)."

    def do_new_free_key(self, email: str, confirm: bool) -> dict:
        denied = self._backend_allowed(email, confirm)
        if denied:
            return {"_error": denied}




        def _base_url():
            inst = self._inst()
            client = self._client(inst) if inst else None
            return getattr(client, "base_url", None) or ""

        from ..data_tools import _run_on_main_thread

        base = _run_on_main_thread(_base_url) or os.environ.get("TERRALAB_BASE_URL") or "https://terra-lab.ai"
        import json
        import urllib.parse
        import urllib.request

        scheme = urllib.parse.urlparse(base).scheme
        if scheme not in ("http", "https"):
            return {"_error": f"activate-free base URL {base!r} has scheme {scheme or 'none'!r}; "
                    "set TERRALAB_BASE_URL to an http:// or https:// address."}
        body = json.dumps({"email": email}).encode("utf-8")
        req = urllib.request.Request(
            f"{base.rstrip('/')}/api/activate-free",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:





            answer = net.fetch(req, timeout=20, max_bytes=_ACTIVATE_MAX_BYTES,
                               total_timeout=30, polite=False)
            payload = json.loads(answer.body or b"{}")
        except Exception as err:
            return {"_error": f"activate-free call failed: {err}"}

        raw_key = payload.get("key") or payload.get("activationKey") or ""
        fingerprint = f"…{raw_key[-4:]}" if isinstance(raw_key, str) and len(raw_key) >= 4 else None
        return {"ok": True, "key_fingerprint": fingerprint, "server_message": payload.get("message")}




_ADAPTER = AiEditAdapter()


def register_ai_edit_adapter_tools(registry, include_dev: bool = False):
    """The AI Edit bridge."""










    from ...core.tool_registry import Tool

    def _state(args):
        return _ADAPTER.state()

    if include_dev:
        _register_ai_edit_dev_tools(registry, Tool, _state)
    _register_ai_edit_user_tools(registry, Tool)


def _register_ai_edit_dev_tools(registry, Tool, _state):
    registry.register(Tool(
        name="ai_edit_debug_state",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_state,
    ))
    registry.register(Tool(
        name="ai_edit_signout",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda a: _ADAPTER.do_signout(),
    ))
    registry.register(Tool(
        name="ai_edit_set_key",
        input_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "validate_with_server": {
                    "type": "boolean",
                },
            },
            "required": ["key"],
        },
        handler=lambda a: _ADAPTER.do_set_key(a.get("key", ""), a.get("validate_with_server", False)),
    ))
    registry.register(Tool(
        name="ai_edit_mock_usage",
        input_schema={
            "type": "object",
            "properties": {
                "used": {"type": "integer"},
                "limit": {"type": "integer"},
                "is_free": {"type": "boolean"},
            },
            "required": ["used", "limit"],
        },
        handler=lambda a: _ADAPTER.do_mock_usage(a.get("used"), a.get("limit"), a.get("is_free", True)),
    ))
    registry.register(Tool(
        name="ai_edit_restore_usage",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda a: _ADAPTER.do_restore_usage(),
    ))
    registry.register(Tool(
        name="ai_edit_set_dev_flags",
        input_schema={
            "type": "object",
            "properties": {
                "debug": {"type": "boolean"},
                "skip_trial": {"type": "boolean"},
            },
            "required": [],
        },
        handler=lambda a: _ADAPTER.do_set_dev_flags(a.get("debug"), a.get("skip_trial")),
    ))
    registry.register(Tool(
        name="ai_edit_simulate_signup",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda a: _ADAPTER.do_simulate_signup(),
    ))


def _register_ai_edit_user_tools(registry, Tool):
    registry.register(Tool(
        name="ai_edit_run_generation",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "bbox": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "use_canvas_extent": {
                    "type": "boolean",
                },
                "resolution": {
                    "type": "string",
                    "enum": ["1K", "2K", "4K"],
                },
                "reference_layers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["prompt"],
        },
        handler=lambda a: _ADAPTER.do_run_generation(a),
    ))
    registry.register(Tool(
        name="ai_edit_generation_status",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda a: _ADAPTER.do_generation_status(),
    ))
    registry.register(Tool(
        name="ai_edit_attach_reference",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                },
                "layer_name": {
                    "type": "string",
                },
                "use_zone_extent": {
                    "type": "boolean",
                },
            },
            "required": [],
        },
        handler=lambda a: _ADAPTER.do_attach_reference(
            a.get("path", ""), a.get("layer_name", ""), a.get("use_zone_extent", True)
        ),
    ))
    registry.register(Tool(
        name="ai_edit_set_resolution",
        input_schema={
            "type": "object",
            "properties": {
                "resolution": {
                    "type": "string",
                    "enum": ["1K", "2K", "4K"],
                },
            },
            "required": ["resolution"],
        },
        handler=lambda a: _ADAPTER.do_set_resolution(a.get("resolution", "")),
    ))
    registry.register(Tool(
        name="ai_edit_set_model",
        input_schema={
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                },
            },
            "required": ["model_id"],
        },
        handler=lambda a: _ADAPTER.do_set_model(a.get("model_id", "")),
    ))
    registry.register(Tool(
        name="ai_edit_clear_references",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda a: _ADAPTER.do_clear_references(),
    ))
    registry.register(Tool(
        name="ai_edit_backend_new_free_key",
        input_schema={
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
            "required": ["email", "confirm"],
        },
        handler=lambda a: _ADAPTER.do_new_free_key(a.get("email", ""), a.get("confirm", False)),


        background=True,
    ))
