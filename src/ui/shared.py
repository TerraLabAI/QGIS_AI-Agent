# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What every module of the panel needs and no other module owns."""







from __future__ import annotations

import importlib
import os

from qgis.PyQt.QtCore import QCoreApplication, QObject, QTimer
from qgis.PyQt.QtWidgets import QDialog, QMenu

PRODUCT_ID = "ai-agent"
PRODUCT_NAME = "AI Agent"
PLUGIN_TITLE = "AI Agent by TerraLab"







PLUGIN_FOLDERS = ("AI_Agent", "QGIS_AI-Agent-Team", "QGIS_AI-Agent", "QGIS_AI_Agent")




TR_CONTEXT = "AIAgent"


def tr(text: str, disambiguation: str | None = None, n: int = -1) -> str:
    """Translate a string that lives outside a QObject method."""
    return QCoreApplication.translate(TR_CONTEXT, text, disambiguation, n)


def exec_dialog(dialog):
    """Run a modal dialog on Qt5 and on Qt6, and return its result code."""













    return QDialog.exec(dialog)


def exec_menu(menu, pos):
    """Pop up a context menu on Qt5 and on Qt6, and return the chosen action."""










    return QMenu.exec(menu, pos)



UI_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(os.path.dirname(UI_DIR))
ICONS_DIR = os.path.join(PLUGIN_DIR, "icons")
PLUGIN_ICON_PATH = os.path.join(ICONS_DIR, "icon.png")
TERRALAB_LOGO_PATH = os.path.join(ICONS_DIR, "terralab-logo.png")









PLUGIN_CACHE_DIR = os.path.normpath(
    os.environ.get("AI_AGENT_CACHE_DIR")
    or os.path.join(os.path.expanduser("~/.qgis_ai-agent"), "ui-cache")
)


_LEGACY_CACHE_DIR = os.path.normpath(os.path.expanduser("~/.qgis_ai_agent"))


def drop_legacy_cache_dir() -> bool:
    """Remove the misspelled cache folder, once."""







    import shutil

    if _LEGACY_CACHE_DIR == PLUGIN_CACHE_DIR or not os.path.isdir(_LEGACY_CACHE_DIR):
        return True
    import stat

    failed = []

    def _retry(func, target, _exc):



        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            failed.append(target)

    try:
        shutil.rmtree(_LEGACY_CACHE_DIR, onexc=_retry)
    except TypeError:
        shutil.rmtree(_LEGACY_CACHE_DIR, onerror=_retry)
    except OSError:
        return False
    return not failed


SITE_URL = "https://terra-lab.ai"


def build_utm_url(path: str, utm_content: str) -> str:
    """Campaign-tagged terra-lab.ai URL; CTAs differ only by path and content."""
    return (
        f"{SITE_URL}{path}"
        f"?utm_source=qgis&utm_medium=plugin&utm_campaign={PRODUCT_ID}"
        f"&utm_content={utm_content}"
    )


DASHBOARD_URL = build_utm_url(f"/dashboard/{PRODUCT_ID}", "dashboard")
UPGRADE_URL = build_utm_url(f"/dashboard/{PRODUCT_ID}", "upgrade")




PRICING_URL = build_utm_url(f"/{PRODUCT_ID}#pricing", "pricing")
PRODUCT_URL = build_utm_url(f"/{PRODUCT_ID}", "dock_branding")
TERMS_URL = build_utm_url("/terms-of-sale", "settings_terms")
PRIVACY_URL = build_utm_url("/privacy-policy", "settings_privacy")



TUTORIAL_URL = build_utm_url("/blog/ai-agent-complete-guide", "tutorial")
TERRALAB_MORE_URL = build_utm_url("", "menu_more")




SITE_HOME_URL = build_utm_url("", "settings_site")
SUPPORT_EMAIL = "yvann.barbot@terra-lab.ai"


BOOK_A_CALL_URL = ""




FREE_RUNS = 10
PRO_RUNS_PER_MONTH = 250


PRO_PRICE = "49 EUR"
PRO_PRICE_LINE = "49 EUR a month"




_served_config: dict = {}


def set_server_config(payload: dict | None) -> None:
    """Apply the safe scalar subset of /api/plugin/config for this session."""
    global _served_config
    if not isinstance(payload, dict):
        return
    _served_config = dict(payload)



    from ..core.privacy_notice import set_served_notice_version

    set_served_notice_version(get_privacy_notice_version())


_connectors: list = []

_connectors_version = 0














_CONNECTOR_FIELDS = ("id", "name", "glyph", "url", "datasets", "kinds", "licence",
                     "coverage", "attribution", "highlights", "summary", "terms_url",
                     "status", "status_label", "caveat", "prompts",
                     "tagline", "category", "category_label", "popular",
                     "country", "also_covers", "languages", "tools")


def _connector_store():
    """The settings object holding the cache, or None when core is not ready."""




    try:
        from ..core.settings import Settings

        return Settings()
    except Exception as exc:  # noqa: BLE001
        _connector_warning(f"Connector cache unavailable: {exc}")
        return None


def _connector_warning(message: str) -> None:
    try:
        from ..core.logger import log_warning

        log_warning(message)
    except Exception:  # nosec B110 - the cache is best effort, never a failure the user sees
        pass


def _clean_connectors(rows) -> list:
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        out.append({k: row[k] for k in _CONNECTOR_FIELDS if k in row})
    return out


def set_connectors(rows) -> None:
    """Keep the data sources the server named in its session frame."""








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
    """The known data sources, copied."""




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


def _bump_connectors() -> None:
    global _connectors_version
    _connectors_version += 1
    _CALL_MATCH_CACHE.clear()





_CONNECTOR_ARG_KEYS = ("url", "provider", "source", "collection", "portal",
                       "endpoint", "catalog", "service", "dataset")






def _tools_of(connector) -> list:
    return [str(t).lower() for t in (connector.get("tools") or []) if isinstance(t, str)]




_CALL_MATCH_CACHE: dict = {}
_CALL_MATCH_MAX = 256


def connector_for_call(name: str, args) -> dict | None:
    """The known connector a tool call reached, or None."""







    values = []
    if isinstance(args, dict):
        for key in _CONNECTOR_ARG_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip().lower())
    tool = str(name or "").lower()
    get_connectors()
    connectors = _connectors
    key = (_connectors_version, tool, tuple(values))
    hit = _CALL_MATCH_CACHE.get(key, _CALL_MATCH_CACHE)
    if hit is not _CALL_MATCH_CACHE:
        return dict(hit) if hit is not None else None
    found = _match_connector(connectors, tool, values)
    if len(_CALL_MATCH_CACHE) >= _CALL_MATCH_MAX:
        _CALL_MATCH_CACHE.clear()
    _CALL_MATCH_CACHE[key] = found
    return dict(found) if found is not None else None


def _match_connector(connectors, tool: str, values: list) -> dict | None:
    from urllib.parse import urlsplit

    for connector in connectors:
        if tool and tool in _tools_of(connector):
            return dict(connector)
    for connector in connectors:
        cid = str(connector.get("id") or "").strip().lower()
        cname = str(connector.get("name") or "").strip().lower()
        host = ""
        try:
            host = (urlsplit(str(connector.get("url") or "")).hostname or "").lower()
        except ValueError:
            host = ""
        if host.startswith("www."):
            host = host[4:]
        if cid and len(cid) >= 4 and cid in tool:
            return dict(connector)
        for value in values:
            if host and host in value:
                return dict(connector)
            if cid and (value == cid or value.startswith(cid + ":") or value.startswith(cid + "/")):
                return dict(connector)
            if cname and len(cname) >= 3 and cname in value:
                return dict(connector)
    return None




_qgis_plugins: list = []
_hidden_plugins: list = []


_hidden_plugins_received = False
_tool_names: list = []





_PLUGIN_FIELDS = ("folder", "folders", "name", "author", "licence", "url", "category",
                  "summary", "skill", "prompts", "glyph", "providers")


def set_qgis_plugins(rows) -> None:
    """Keep what the server knows about QGIS plugins, from the session frame."""





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
    """What the server knows about QGIS plugins, copied, cache included."""
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
    """The server's rows, reachable under every folder name each one installs as."""













    out: dict = {}
    for row in get_qgis_plugins() or []:
        names = [str(n) for n in (row.get("folders") or ()) if n]
        for name in [str(row.get("folder") or "")] + names:
            if name:
                out.setdefault(name, row)
    return out











DEFAULT_HIDDEN_PLUGINS: tuple = ()


def set_hidden_plugins(folders) -> None:
    """Plugin folders the Connectors directory leaves out, from the session frame."""






    global _hidden_plugins, _hidden_plugins_received
    cleaned = sorted({str(f).strip() for f in (folders or []) if str(f or "").strip()})
    _hidden_plugins = cleaned
    _hidden_plugins_received = True




    store = _connector_store()
    if store is None:
        return
    try:
        store.hidden_plugins = cleaned
    except Exception as exc:  # noqa: BLE001
        _connector_warning(f"Hidden plugin list not cached: {exc}")


def get_hidden_plugins() -> set:
    """The folders to leave out: the server's list, else the cache, else none."""





    global _hidden_plugins, _hidden_plugins_received
    if _hidden_plugins_received:
        return set(_hidden_plugins)
    if not _hidden_plugins:
        store = _connector_store()
        if store is not None:
            try:
                cached = store.hidden_plugins
                if cached is not None:
                    _hidden_plugins = [str(f) for f in (cached or [])]
                    _hidden_plugins_received = True
                    return set(_hidden_plugins)
            except Exception as exc:  # noqa: BLE001
                _connector_warning(f"Hidden plugin cache not read: {exc}")
    return set(_hidden_plugins) or set(DEFAULT_HIDDEN_PLUGINS)


def set_tool_names(names) -> None:
    """Remember the tool catalog so Settings can describe it without rebuilding it."""






    global _tool_names
    _tool_names = sorted({str(name) for name in (names or []) if str(name or "").strip()})
    if not _tool_names:
        return
    store = _connector_store()
    if store is None:
        return
    try:
        store.known_tool_names = _tool_names
    except Exception as exc:  # noqa: BLE001
        _connector_warning(f"Tool names not cached: {exc}")


def get_tool_names() -> list:
    global _tool_names
    if not _tool_names:
        store = _connector_store()
        if store is not None:
            try:
                _tool_names = [str(n) for n in (store.known_tool_names or [])]
            except Exception as exc:  # noqa: BLE001
                _connector_warning(f"Tool names not read: {exc}")
    return list(_tool_names)


def server_config() -> dict:
    """The last served config, copied so callers cannot mutate shared state."""
    return dict(_served_config)


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
    try:
        value = int(value)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def get_dashboard_url() -> str:
    return _served_url("dashboard_url", DASHBOARD_URL)


def get_upgrade_url() -> str:
    return _served_url("upgrade_url", _served_url("subscribe_url", UPGRADE_URL))


def get_pricing_url() -> str:
    """The plans and the price, on the site."""






    return _served_url("plans_url", _served_url("pricing_url", PRICING_URL))


def get_tutorial_url() -> str:
    return _served_url("tutorial_url", TUTORIAL_URL)







LEARN_ITEMS = (
    {
        "kind": "article",
        "title": "The complete AI Agent guide",
        "note": "The panel from the first prompt to the finished map.",
        "url": TUTORIAL_URL,
        "thumbnail_url": "",
    },
    {
        "kind": "article",
        "title": "More on the blog",
        "note": "What it can do, what it asks before doing, and how to undo a run.",
        "url": build_utm_url("/blog", "settings_learn"),
        "thumbnail_url": "",
    },
)


def get_learn_items() -> list:
    """The tutorials for Settings > Tutorials, the server's list when it sends one."""





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
    return rows or [dict(item) for item in LEARN_ITEMS]


def get_product_url() -> str:
    """The AI Agent landing page: what the wordmark in the Settings rail opens."""
    return _served_url("product_url", PRODUCT_URL)


def get_site_url() -> str:
    """terra-lab.ai itself: what the line at the foot of the rail opens."""
    return _served_url("site_url", SITE_HOME_URL)


def get_terms_url() -> str:
    return _served_url("terms_url", TERMS_URL)


def get_privacy_url() -> str:
    return _served_url("privacy_url", PRIVACY_URL)


def get_latest_version() -> str:
    """The version the server says is published, or "" (say nothing)."""
    return _served_text("latest_version", "")


def get_min_supported_version() -> str:
    """The oldest version the servers still answer normally, or ""."""
    return _served_text("min_supported_version", "")


def get_release_notes_line() -> str:
    """One served line saying what the latest version brings, or ""."""
    return _served_text("release_notes_line", _served_text("update_message", ""))[:160]


UPDATE_POLICIES = ("require", "recommend")


def get_update_policy() -> str:
    """How hard the panel pushes an installable update: ``require`` (the chat is replaced by the update card until it is done) or ``recommend`` (a."""




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
        pro = pricing.get("pro")
        if isinstance(pro, dict):
            try:
                value = int(pro.get("runs_per_month"))
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    return _served_int("pro_runs_per_month", PRO_RUNS_PER_MONTH)


def get_pro_price() -> str:
    """The monthly price of Pro, "49 EUR": ``pricing.pro.price`` when the website serves one, the shipped figure otherwise."""

    pricing = _served_config.get("pricing")
    if isinstance(pricing, dict):
        pro = pricing.get("pro")
        if isinstance(pro, dict):
            value = pro.get("price")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return _served_text("pro_price", PRO_PRICE)


def get_pro_price_line() -> str:
    pricing = _served_config.get("pricing")
    if isinstance(pricing, dict):
        value = pricing.get("pro_price_line")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _served_text("pro_price_line", PRO_PRICE_LINE)









FREE_PLAN_POINTS = (


    "{n} runs a month",



    "Every tool, every connector, every QGIS plugin",
    "Fast model",
    "Personal, non-commercial use",
)
PRO_PLAN_POINTS = (
    "{n} runs a month",
    "The Pro model on the hard tasks",
    "Memory and custom instructions",
    "Longer runs on complex tasks",
    "Commercial use",
)


def _served_points(key: str, fallback: tuple) -> list:
    pricing = _served_config.get("pricing")
    plan = pricing.get(key) if isinstance(pricing, dict) else None
    points = plan.get("points") if isinstance(plan, dict) else None
    if isinstance(points, list):
        clean = [str(p).strip()[:120] for p in points if str(p or "").strip()]
        if clean:
            return clean[:6]
    return list(fallback)


def get_free_plan_points() -> list:
    """What the free grant gives. ``{n}`` is the run count, filled in by the page."""
    return _served_points("free", FREE_PLAN_POINTS)


def get_pro_plan_points() -> list:
    """What Pro gives. ``{n}`` is the monthly run count, filled in by the page."""
    return _served_points("pro", PRO_PLAN_POINTS)








PAYWALL_POINTS = (
    "{n} runs a month",



    "Go back to the project as it was before a run",
    "Cancel anytime",
)


def get_paywall_points() -> list:
    """The three lines under the Upgrade button, served or shipped."""
    pricing = _served_config.get("pricing")
    plan = pricing.get("pro") if isinstance(pricing, dict) else None
    points = plan.get("paywall_points") if isinstance(plan, dict) else None
    if isinstance(points, list):
        clean = [str(p).strip()[:120] for p in points if str(p or "").strip()]
        if clean:
            return clean[:6]
    return list(PAYWALL_POINTS)








_MAX_PLAN_NAME_CHARS = 40


def get_plan_name(key: str, shipped: str, field: str = "name") -> str:
    """The name of the free or the pro plan, served or shipped."""
    pricing = _served_config.get("pricing")
    plan = pricing.get(key) if isinstance(pricing, dict) else None
    value = plan.get(field) if isinstance(plan, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()[:_MAX_PLAN_NAME_CHARS]
    return shipped









_MAX_LABEL_ROWS = 80
_MAX_LABEL_CHARS = 200


def served_label_map(key: str) -> dict:
    """One `{token: words}` map from /api/plugin/config. Empty when unserved."""
    raw = _served_config.get(key)
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for token, words in list(raw.items())[:_MAX_LABEL_ROWS]:
        text = str(words or "").strip()
        if isinstance(token, str) and token.strip() and text:
            out[token.strip()] = text[:_MAX_LABEL_CHARS]
    return out













_MAX_EFFORT_NAME_CHARS = 24
_MAX_EFFORT_NOTE_CHARS = 200


def get_effort_text(level: str, name: str, note: str) -> tuple:
    """``(name, note)`` for one effort level, served or shipped."""
    efforts = _served_config.get("efforts")
    row = efforts.get(level) if isinstance(efforts, dict) else None
    if not isinstance(row, dict):
        return (name, note)
    served_name = str(row.get("name") or "").strip()[:_MAX_EFFORT_NAME_CHARS]
    served_note = str(row.get("note") or "").strip()[:_MAX_EFFORT_NOTE_CHARS]
    return (served_name or name, served_note or note)














_MAX_NOTICE_ROWS = 5
_MAX_LEAD_CHARS = 140
_MAX_REST_CHARS = 400


def get_privacy_notice_heading(shipped: str) -> str:
    """The dialog's title line, served or shipped."""
    notice = _served_config.get("privacy_notice")
    value = notice.get("heading") if isinstance(notice, dict) else None
    return value.strip()[:_MAX_LEAD_CHARS] if isinstance(value, str) and value.strip() else shipped


def get_privacy_notice_rows(shipped: tuple) -> tuple:
    """``((kind, lead, rest), ...)``: the served rows, else the shipped ones."""





    notice = _served_config.get("privacy_notice")
    rows = notice.get("rows") if isinstance(notice, dict) else None
    if not isinstance(rows, list) or not rows:
        return shipped
    out: list = []
    for row in rows[:_MAX_NOTICE_ROWS]:
        if not isinstance(row, dict):
            return shipped
        lead = str(row.get("lead") or "").strip()
        rest = str(row.get("rest") or "").strip()
        if not lead or not rest:
            return shipped
        kind = str(row.get("kind") or "").strip().lower()[:20]
        out.append((kind, lead[:_MAX_LEAD_CHARS], rest[:_MAX_REST_CHARS]))
    return tuple(out) or shipped


def get_privacy_notice_version() -> int:
    """The notice version the server is on, never below the one we shipped."""






    from ..core.privacy_notice import PRIVACY_NOTICE_VERSION

    notice = _served_config.get("privacy_notice")
    value = notice.get("version") if isinstance(notice, dict) else None
    try:
        served = int(value)
    except (TypeError, ValueError):
        return PRIVACY_NOTICE_VERSION
    if served > 1000 or served < 0:
        return PRIVACY_NOTICE_VERSION
    return max(PRIVACY_NOTICE_VERSION, served)


def plugin_version() -> str:
    """The version line of metadata.txt, or "" when it cannot be read."""
    try:
        with open(os.path.join(PLUGIN_DIR, "metadata.txt"), encoding="utf-8") as handle:
            for line in handle:
                if line.strip().lower().startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""






def resolve_qt_enum(parent, scope: str | None, name: str):
    """A scoped enum member on Qt6, the flat one on an older Qt5."""
    if scope:
        scoped = getattr(getattr(parent, scope, None), name, None)
        if scoped is not None:
            return scoped
    return getattr(parent, name)


def qt_enum_int(parent, scope: str | None, name: str, fallback: int) -> int:
    """The plain int of a Qt enum member, for an API a binding types as int."""







    try:
        member = resolve_qt_enum(parent, scope, name)
    except AttributeError:
        return int(fallback)
    value = getattr(member, "value", member)
    return int(value) if isinstance(value, int) else int(fallback)


def _import_moved(name: str):
    """A class Qt6 relocated from QtWidgets to QtGui, found in either."""
    for module in ("qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets"):
        found = getattr(importlib.import_module(module), name, None)
        if found is not None:
            return found
    raise ImportError(f"{name} not found in qgis.PyQt.QtGui or QtWidgets")


QAction = _import_moved("QAction")
QActionGroup = _import_moved("QActionGroup")
QShortcut = _import_moved("QShortcut")


def event_pos(event):
    """A QPoint for a mouse event on both Qt5 and Qt6."""
    getter = getattr(event, "position", None) or getattr(event, "localPos", None)
    getter = getter or getattr(event, "pos")  # noqa: B009
    point = getter()
    to_point = getattr(point, "toPoint", None)
    return to_point() if to_point is not None else point


def safe_disconnect(owner, signal_name: str, slot=None) -> bool:
    """Disconnect one signal, swallowing the three teardown faults."""




    try:
        signal = getattr(owner, signal_name)
        if slot is None:
            signal.disconnect()
        else:
            signal.disconnect(slot)
        return True
    except (TypeError, RuntimeError, AttributeError):
        return False


def safe_single_shot(msec: int, owner: QObject, callback) -> QTimer:
    """A single-shot timer that dies with ``owner``."""





    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(callback)
    timer.start(max(0, int(msec)))
    return timer


def is_deleted(widget) -> bool:
    """True when the C++ side of ``widget`` is gone."""
    try:
        from qgis.PyQt import sip

        return widget is None or sip.isdeleted(widget)
    except (ImportError, TypeError, RuntimeError):
        return widget is None


def format_reset_date(period_end_iso: str) -> str:
    """The reset date in the user's own date format, "" when unreadable."""




    from qgis.PyQt.QtCore import QDate, QDateTime, QLocale, Qt

    if not period_end_iso:
        return ""
    stamp = QDateTime.fromString(str(period_end_iso), Qt.DateFormat.ISODate)
    if not stamp.isValid():
        date = QDate.fromString(str(period_end_iso)[:10], "yyyy-MM-dd")
    else:
        date = stamp.toLocalTime().date()
    if not date.isValid():
        return ""
    return QLocale().toString(date, QLocale.FormatType.ShortFormat)







_SCREEN_EDGE_MARGIN_PX = 48


def available_screen_rect(widget):
    """The usable screen area for *widget*, or None when Qt will not say."""
    try:
        screen = widget.screen()
    except (AttributeError, RuntimeError):
        screen = None
    if screen is None:
        from qgis.PyQt.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    available = screen.availableGeometry()
    return None if available.isEmpty() else available


def size_within_screen(dialog, width: int, height: int,
                       min_width: int = 0, min_height: int = 0) -> None:
    """Resize *dialog*, and set its minimum, without either exceeding the screen."""
    available = available_screen_rect(dialog)
    if available is not None:
        cap_w = max(1, available.width() - _SCREEN_EDGE_MARGIN_PX)
        cap_h = max(1, available.height() - _SCREEN_EDGE_MARGIN_PX)
        width, height = min(width, cap_w), min(height, cap_h)
        min_width, min_height = min(min_width, cap_w), min(min_height, cap_h)
    if min_width or min_height:
        dialog.setMinimumSize(min_width, min_height)
    dialog.resize(width, height)


def watch_screen_changes(root) -> bool:
    """Redraw *root* and its children when the window moves to another screen."""




















    from qgis.PyQt.QtCore import QEvent
    from qgis.PyQt.QtWidgets import QApplication, QWidget

    try:
        window = root.window()
        handle = window.windowHandle() if window is not None else None
    except (AttributeError, RuntimeError):
        return False
    if handle is None:
        return False
    if getattr(root, "_screen_watch", None) is not None:
        return True

    def repaint(_screen=None):
        try:
            targets = [root] + list(root.findChildren(QWidget))
        except RuntimeError:
            return
        for widget in targets:
            try:
                QApplication.sendEvent(widget, QEvent(QEvent.Type.PaletteChange))
            except (RuntimeError, TypeError):
                continue

    try:
        handle.screenChanged.connect(repaint)
    except (AttributeError, TypeError):
        return False


    root._screen_watch = repaint
    return True
