# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

import re
import string

from . import tuning
from .numbers import as_int
from .privacy_notice import PRIVACY_NOTICE_VERSION, set_served_notice_version

PRODUCT_ID = "ai-agent"

SITE_URL = "https://terra-lab.ai"


def build_utm_url(path: str, utm_content: str, anchor: str = "") -> str:

    return (
        f"{SITE_URL}{path}"
        f"?utm_source=qgis&utm_medium=plugin&utm_campaign={PRODUCT_ID}"
        f"&utm_content={utm_content}"
        + (f"#{anchor}" if anchor else "")
    )


DASHBOARD_URL = build_utm_url(f"/dashboard/{PRODUCT_ID}", "dashboard")
UPGRADE_URL = build_utm_url(f"/dashboard/{PRODUCT_ID}", "upgrade")



PRICING_URL = build_utm_url(f"/{PRODUCT_ID}", "pricing", "pricing")
PRODUCT_URL = build_utm_url(f"/{PRODUCT_ID}", "dock_branding")
TERMS_URL = build_utm_url("/terms-of-sale", "settings_terms")
PRIVACY_URL = build_utm_url("/privacy-policy", "settings_privacy")

DICTATION_PRIVACY_URL = build_utm_url("/privacy-policy", "dictation_privacy", "voice-dictation")

TUTORIAL_URL = build_utm_url("/blog/ai-agent-complete-guide", "tutorial")
TERRALAB_MORE_URL = build_utm_url("", "menu_more")


SITE_HOME_URL = build_utm_url("", "settings_site")
SUPPORT_EMAIL = "yvann.barbot@terra-lab.ai"


BOOK_A_CALL_URL = ""





FREE_RUNS = 5


LEGACY_FREE_RUNS_MAX = 20
PRO_RUNS_PER_MONTH = 300







_served_config: dict = {}


def set_server_config(payload: dict | None) -> None:

    global _served_config
    if not isinstance(payload, dict):
        return
    _served_config = dict(payload)



    set_served_notice_version(get_privacy_notice_version())


    try:
        from .settings import set_served_server_urls

        set_served_server_urls(payload.get("ws_urls"))
    except Exception as exc:  # noqa: BLE001
        _connector_warning(f"Alternate server addresses not applied: {exc}")


_connectors: list = []

_connectors_version = 0







_CONNECTOR_FIELDS = ("id", "name", "glyph", "url", "datasets", "kinds", "licence",
                     "coverage", "attribution", "highlights", "summary", "terms_url",
                     "status", "status_label", "caveat", "prompts",
                     "tagline", "category", "category_label", "popular",
                     "country", "also_covers", "languages", "tools", "logo_url", "hosts",
                     "countries", "key_held", "rank", "region", "region_label", "flags",
                     "group", "group_label", "stand_in")


def _connector_store():





    try:
        from .settings import Settings

        return Settings()
    except Exception as exc:  # noqa: BLE001
        _connector_warning(f"Connector cache unavailable: {exc}")
        return None


def _connector_warning(message: str) -> None:
    try:
        from .logger import log_warning

        log_warning(message)
    except Exception:  # nosec B110
        pass


def _clean_connectors(rows) -> list:
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        out.append({k: row[k] for k in _CONNECTOR_FIELDS if k in row})
    return out


def set_connectors(rows) -> None:









    global _connectors
    cleaned = _clean_connectors(rows)
    if cleaned == _connectors:
        return
    _connectors = cleaned
    _bump_connectors()
    if not _connectors:
        return
    store = _connector_store()
    if store is None:
        return
    try:
        store.known_connectors = _connectors
    except Exception as exc:  # noqa: BLE001
        _connector_warning(f"Connector list not cached: {exc}")


def get_connectors() -> list:





    global _connectors
    if not _connectors:
        store = _connector_store()
        if store is not None:
            try:
                _connectors = _clean_connectors(store.known_connectors)
            except Exception as exc:  # noqa: BLE001
                _connector_warning(f"Connector cache not read: {exc}")
            else:
                _bump_connectors()
    return [dict(r) for r in _connectors]




_connector_watchers: list = []


def watch_connectors(callback):


    _connector_watchers.append(callback)

    def unwatch() -> None:
        if callback in _connector_watchers:
            _connector_watchers.remove(callback)

    return unwatch


def _bump_connectors() -> None:
    global _connectors_version
    _connectors_version += 1
    _CALL_MATCH_CACHE.clear()
    for callback in list(_connector_watchers):
        try:
            callback()
        except Exception as exc:  # noqa: BLE001
            _connector_warning(f"Connector watcher failed: {exc}")





_CONNECTOR_ARG_KEYS = ("url", "provider", "source", "collection", "portal",
                       "endpoint", "catalog", "service", "dataset", "stac_url")




def _theme_source(args) -> str:






    theme = args.get("theme") if isinstance(args, dict) else None
    if not isinstance(theme, str) or not theme.strip():
        return ""
    return "openstreetmap" if theme.strip() in tuning.service_list("osm_themes", ()) else ""


def _tools_of(connector) -> list:
    return [str(t).lower() for t in (connector.get("tools") or []) if isinstance(t, str)]




_CALL_MATCH_CACHE: dict = {}
_CALL_MATCH_MAX = 256


def connector_for_call(name: str, args) -> dict | None:









    values = []
    if isinstance(args, dict):
        for key in _CONNECTOR_ARG_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip().lower())
    tool = str(name or "").lower()
    get_connectors()
    connectors = _connectors
    source = _theme_source(args)
    key = (_connectors_version, tool, tuple(values), source)
    hit = _CALL_MATCH_CACHE.get(key, _CALL_MATCH_CACHE)
    if hit is not _CALL_MATCH_CACHE:
        return dict(hit) if hit is not None else None
    found = None
    if source:
        found = _match_connector(connectors, "", [source])
    if found is None:
        found = _match_connector(connectors, tool, values)
    if len(_CALL_MATCH_CACHE) >= _CALL_MATCH_MAX:
        _CALL_MATCH_CACHE.clear()
    _CALL_MATCH_CACHE[key] = found
    return dict(found) if found is not None else None


def _host_and_path(url: str) -> tuple:

    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url if "//" in url else "//" + url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return "", ""
    if host.startswith("www."):
        host = host[4:]
    return host, parts.path.rstrip("/").lower()


def _url_within(value: str, host: str, path: str) -> bool:






    if "/" not in value and "." not in value:
        return False
    got_host, got_path = _host_and_path(value)
    if not got_host or not (got_host == host or got_host.endswith("." + host)):
        return False
    return not path or got_path == path or got_path.startswith(path + "/")


def _served_hosts(connector) -> list:

    return [str(h).lower() for h in (connector.get("hosts") or []) if isinstance(h, str) and h]


def _at_connector(value: str, connector) -> bool:

    host, path = _host_and_path(str(connector.get("url") or ""))
    if host and _url_within(value, host, path):
        return True
    return any(_url_within(value, h, "") for h in _served_hosts(connector))


def _match_connector(connectors, tool: str, values: list) -> dict | None:
    addresses = [v for v in values if v.startswith(("https://", "http://"))]



    for value in addresses:
        for connector in connectors:
            if _at_connector(value, connector):
                return dict(connector)
    for connector in connectors:
        if tool and tool in _tools_of(connector) and not addresses:
            return dict(connector)
    for connector in connectors:
        cid = str(connector.get("id") or "").strip().lower()
        cname = str(connector.get("name") or "").strip().lower()
        if cid and len(cid) >= 4 and cid in tool:
            return dict(connector)
        for value in values:
            if _at_connector(value, connector):
                return dict(connector)
            if cid and (value == cid or value.startswith(cid + ":") or value.startswith(cid + "/")):
                return dict(connector)
            if cname and len(cname) >= 3 and cname in value:
                return dict(connector)
    return None


_qgis_plugins: list = []
_hidden_plugins: list = []


_hidden_state = {"received": False}





_PLUGIN_FIELDS = ("folder", "folders", "name", "author", "licence", "url", "category",
                  "summary", "prompts", "glyph")


def set_qgis_plugins(rows) -> None:






    global _qgis_plugins
    cleaned = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("folder"):
            cleaned.append({k: row[k] for k in _PLUGIN_FIELDS if k in row})
    _qgis_plugins = cleaned
    if not cleaned:
        return
    store = _connector_store()
    if store is None:
        return
    try:
        store.known_qgis_plugins = cleaned
    except Exception as exc:  # noqa: BLE001
        _connector_warning(f"Plugin list not cached: {exc}")


def get_qgis_plugins() -> list:

    global _qgis_plugins
    if not _qgis_plugins:
        store = _connector_store()
        if store is not None:
            try:
                _qgis_plugins = [r for r in (store.known_qgis_plugins or []) if isinstance(r, dict)]
            except Exception as exc:  # noqa: BLE001
                _connector_warning(f"Plugin cache not read: {exc}")
    return [dict(r) for r in _qgis_plugins]


def known_plugins_by_folder() -> dict:












    out: dict = {}
    for row in get_qgis_plugins() or []:
        names = [str(n) for n in (row.get("folders") or ()) if n]
        for name in [str(row.get("folder") or "")] + names:
            if name:
                out.setdefault(name, row)
    return out











DEFAULT_HIDDEN_PLUGINS: tuple = ()


def set_hidden_plugins(folders) -> None:







    global _hidden_plugins
    cleaned = sorted({str(f).strip() for f in (folders or []) if str(f or "").strip()})
    _hidden_plugins = cleaned
    _hidden_state["received"] = True


    store = _connector_store()
    if store is None:
        return
    try:
        store.hidden_plugins = cleaned
    except Exception as exc:  # noqa: BLE001
        _connector_warning(f"Hidden plugin list not cached: {exc}")


def get_hidden_plugins() -> set:






    global _hidden_plugins
    if _hidden_state["received"]:
        return set(_hidden_plugins)
    if not _hidden_plugins:
        store = _connector_store()
        if store is not None:
            try:
                cached = store.hidden_plugins
                if cached is not None:
                    _hidden_plugins = [str(f) for f in (cached or [])]
                    _hidden_state["received"] = True
                    return set(_hidden_plugins)
            except Exception as exc:  # noqa: BLE001
                _connector_warning(f"Hidden plugin cache not read: {exc}")
    return set(_hidden_plugins) or set(DEFAULT_HIDDEN_PLUGINS)


def _served_url(key: str, fallback: str) -> str:
    value = _served_config.get(key)
    if isinstance(value, str) and value.startswith("https://"):
        return value
    return fallback


def _served_text(key: str, fallback: str) -> str:
    value = _served_config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _served_int(key: str, fallback: int) -> int:
    value = _served_config.get(key)
    value = as_int(value)
    if value is None:
        return fallback
    return value if value > 0 else fallback


def get_dashboard_url() -> str:
    return _served_url("dashboard_url", DASHBOARD_URL)


def get_upgrade_url() -> str:
    return _served_url("upgrade_url", _served_url("subscribe_url", UPGRADE_URL))


def get_trial_checkout_url() -> str:


    return _served_url("trial_checkout_url", "")


def get_pricing_url() -> str:







    return _served_url("plans_url", _served_url("pricing_url", PRICING_URL))


def get_tutorial_url() -> str:
    return _served_url("tutorial_url", TUTORIAL_URL)


def get_served_learn_items() -> list:






    served = _served_config.get("learn")
    rows: list = []
    for row in served if isinstance(served, list) else []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        if not url.startswith("https://"):
            continue
        thumbnail = str(row.get("thumbnail_url") or "")
        rows.append({
            "kind": "video" if str(row.get("kind") or "") == "video" else "article",
            "title": str(row.get("title") or "")[:120],
            "note": str(row.get("note") or "")[:200],
            "url": url,
            "thumbnail_url": thumbnail if thumbnail.startswith("https://") else "",
        })
    return rows


def get_product_url() -> str:

    return _served_url("product_url", PRODUCT_URL)


def get_site_url() -> str:

    return _served_url("site_url", SITE_HOME_URL)


def get_terms_url() -> str:
    return _served_url("terms_url", TERMS_URL)


def get_privacy_url() -> str:
    return _served_url("privacy_url", PRIVACY_URL)


def get_latest_version() -> str:

    return _served_text("latest_version", "")


def get_min_supported_version() -> str:

    return _served_text("min_supported_version", "")


def get_release_notes_line() -> str:

    return _served_text("release_notes_line", _served_text("update_message", ""))[:160]


UPDATE_POLICIES = ("require", "recommend")


def get_update_policy() -> str:





    value = _served_text("update_policy", "require").lower()
    return value if value in UPDATE_POLICIES else "require"


def get_support_email() -> str:
    contact = _served_config.get("contact")
    if isinstance(contact, dict):
        value = contact.get("email")
        if isinstance(value, str) and "@" in value:
            return value.strip()
    value = _served_config.get("support_email")
    return value.strip() if isinstance(value, str) and "@" in value else SUPPORT_EMAIL


def get_contact_call_url() -> str:
    contact = _served_config.get("contact")
    if isinstance(contact, dict):
        value = contact.get("call_url")
        if isinstance(value, str) and value.startswith("https://"):
            return value
    return _served_url("contact_call_url", BOOK_A_CALL_URL)


def get_free_runs() -> int:
    return _served_int("free_credits", FREE_RUNS)


def get_pro_runs_per_month() -> int:


    pricing = _served_config.get("pricing")
    if isinstance(pricing, dict):
        for key in ("ai_agent", "pro"):
            plan = pricing.get(key)
            if not isinstance(plan, dict):
                continue
            value = as_int(plan.get("runs_per_month"))
            if value is None:
                continue
            if value > 0:
                return value
    return _served_int("pro_runs_per_month", PRO_RUNS_PER_MONTH)


def get_pro_price() -> str:






    pricing = _served_config.get("pricing")
    plan = pricing.get("ai_agent") if isinstance(pricing, dict) else None
    if not isinstance(plan, dict):
        return ""
    minor = as_int(plan.get("month_minor"))
    if minor is None:
        return ""
    currency = str(pricing.get("currency") or "").strip().upper()
    if minor <= 0 or not re.fullmatch(r"[A-Z]{3}", currency):
        return ""

    amount = f"{minor // 100:,}" if minor % 100 == 0 else f"{minor / 100:,.2f}"

    return f"{amount} {currency}"


def get_promo_active() -> bool:

    return _served_config.get("promo_active") is True






PRO_OFFERS = ("trial", "return", "none")
_OFFER = ["none"]


def set_pro_offer(kind: str) -> None:
    _OFFER[0] = kind if kind in PRO_OFFERS else "none"


def get_pro_offer() -> str:
    return _OFFER[0]





_STUDENT_OFFER: list = [None]


def set_student_offer(raw) -> None:
    offer = None
    if isinstance(raw, dict):
        state = raw.get("state")
        label = raw.get("label")
        url = raw.get("url")
        url = url.strip() if isinstance(url, str) and url.strip().startswith("https://") else ""
        if state in ("offer", "active") and isinstance(label, str) and label.strip():
            if state == "active" or url:

                offer = {"state": state, "label": label.strip()[:120], "url": url,
                         "applied": raw.get("applied") is True}
    _STUDENT_OFFER[0] = offer


def get_student_offer() -> dict | None:
    return _STUDENT_OFFER[0]





_PAYMENT_FAILED: list = [None]


def set_payment_failed(info) -> None:
    _PAYMENT_FAILED[0] = info


def get_payment_failed() -> dict | None:
    return _PAYMENT_FAILED[0]


def get_pro_price_excludes_tax() -> bool:

    pricing = _served_config.get("pricing")
    return isinstance(pricing, dict) and pricing.get("excludes_tax") is True



def _served_points(key: str, fallback: tuple) -> list:
    pricing = _served_config.get("pricing")
    plan = pricing.get(key) if isinstance(pricing, dict) else None
    points = plan.get("points") if isinstance(plan, dict) else None
    if isinstance(points, list):
        clean = [str(p).strip()[:120] for p in points if str(p or "").strip()]
        if clean:
            return clean[:6]
    return list(fallback)




_PRICE_RE = re.compile(r"[€$£¥]|\b(?:EUR|USD|GBP|CHF)\b", re.IGNORECASE)


def get_served_pro_points() -> list:






    runs = get_pro_runs_per_month()
    out = []
    for line in _served_points("pro", ()):
        if _PRICE_RE.search(line):
            continue
        try:
            out.append(line.format(n=runs))
        except (IndexError, KeyError, ValueError):
            out.append(line)
    return out





_MAX_PLAN_NAME_CHARS = 40


def get_plan_name(key: str, shipped: str, field: str = "name") -> str:

    pricing = _served_config.get("pricing")
    plan = pricing.get(key) if isinstance(pricing, dict) else None
    value = plan.get(field) if isinstance(plan, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()[:_MAX_PLAN_NAME_CHARS]
    return shipped








_MAX_LABEL_ROWS = 80
_MAX_LABEL_CHARS = 200

MAX_TOOL_LABELS = 400


def served_label_map(key: str, max_rows: int = _MAX_LABEL_ROWS) -> dict:

    raw = _served_config.get(key)
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for token, words in list(raw.items())[:max_rows]:
        text = str(words or "").strip()
        if isinstance(token, str) and token.strip() and text:
            out[token.strip()] = text[:_MAX_LABEL_CHARS]
    return out











_MAX_OFFER_CHARS = 200
_MAX_OFFER_MAIL_CHARS = 1500


def _fill(template: str, values: dict) -> str | None:



    try:
        parts = list(string.Formatter().parse(template))
    except ValueError:
        return None
    for _text, name, spec, conversion in parts:
        if name is not None and (name not in values or spec or conversion):
            return None
    return template.format(**values)


def offer_text(key: str, shipped: str, **values) -> str:

    raw = _served_config.get("offer_copy")
    raw = raw if isinstance(raw, dict) else {}
    served = raw.get(f"{key}@return") if _OFFER[0] == "return" else None
    served = served if isinstance(served, str) and served.strip() else raw.get(key)
    limit = _MAX_OFFER_MAIL_CHARS if key.endswith(".body") else _MAX_OFFER_CHARS
    if isinstance(served, str) and served.strip() and not _PRICE_RE.search(served):
        filled = _fill(served.strip(), values)
        if filled is not None:
            return filled[:limit]
    try:
        return shipped.format(**values) if values else shipped
    except (IndexError, KeyError, ValueError):
        return shipped












_MAX_EFFORT_NAME_CHARS = 24
_MAX_EFFORT_NOTE_CHARS = 200


def get_effort_text(level: str, name: str, note: str) -> tuple:

    efforts = _served_config.get("efforts")
    row = efforts.get(level) if isinstance(efforts, dict) else None
    if not isinstance(row, dict):
        return (name, note)
    served_name = str(row.get("name") or "").strip()[:_MAX_EFFORT_NAME_CHARS]
    served_note = str(row.get("note") or "").strip()[:_MAX_EFFORT_NOTE_CHARS]
    return (served_name or name, served_note or note)












_MAX_NOTICE_CHECKS = 6
_MAX_LEAD_CHARS = 140
_MAX_TOGGLE_NOTE_CHARS = 200


def _served_notice() -> dict:
    notice = _served_config.get("privacy_notice")
    return notice if isinstance(notice, dict) else {}


def _served_notice_text(key: str, limit: int) -> str:
    value = _served_notice().get(key)
    return value.strip()[:limit] if isinstance(value, str) else ""


def get_privacy_notice_heading(shipped: str) -> str:

    return _served_notice_text("heading", _MAX_LEAD_CHARS) or shipped


def get_privacy_notice_checks(shipped: tuple) -> tuple:

    checks = _served_notice().get("checks")
    if not isinstance(checks, list) or not checks:
        return shipped
    out = []
    for item in checks[:_MAX_NOTICE_CHECKS]:
        text = item.strip() if isinstance(item, str) else ""
        if not text:
            return shipped
        out.append(text[:_MAX_LEAD_CHARS])
    return tuple(out)


def get_privacy_notice_toggle(title: str, note: str) -> tuple:

    served_title = _served_notice_text("toggle_title", _MAX_LEAD_CHARS)
    served_note = _served_notice_text("toggle_note", _MAX_TOGGLE_NOTE_CHARS)
    if served_title and served_note:
        return served_title, served_note
    return title, note


def get_privacy_notice_version() -> int:








    served = as_int(_served_notice().get("version"))
    if served is None:
        return PRIVACY_NOTICE_VERSION
    if served > 1000 or served < 0:
        return PRIVACY_NOTICE_VERSION
    return max(PRIVACY_NOTICE_VERSION, served)
