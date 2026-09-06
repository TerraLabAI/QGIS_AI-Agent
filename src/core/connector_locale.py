# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""How near a national connector is to this machine."""
































from __future__ import annotations

try:  # pragma: no cover - Qt is always there inside QGIS
    from qgis.PyQt.QtCore import QLocale
except Exception:  # noqa: BLE001 - the tests import this without Qt
    QLocale = None

try:  # pragma: no cover - same
    from qgis.core import QgsSettings
except Exception:  # noqa: BLE001
    QgsSettings = None




_REGION_CONTINENT: dict[str, str] = {

    "FR": "EU", "ES": "EU", "DE": "EU", "NL": "EU", "CH": "EU", "GB": "EU",
    "NO": "EU", "AT": "EU", "BE": "EU", "IT": "EU", "EU": "EU",
    "PT": "EU", "IE": "EU", "SE": "EU", "DK": "EU", "FI": "EU", "PL": "EU",
    "CZ": "EU", "SK": "EU", "GR": "EU", "RO": "EU", "BG": "EU", "HU": "EU",
    "LU": "EU", "IS": "EU", "HR": "EU", "SI": "EU", "RS": "EU", "EE": "EU",
    "LV": "EU", "LT": "EU", "UA": "EU", "TR": "EU", "CY": "EU", "MT": "EU",

    "US": "NA", "CA": "NA",
    "MX": "NA", "GT": "NA", "CR": "NA", "PA": "NA", "CU": "NA", "DO": "NA",
    "BR": "SA",
    "AR": "SA", "CL": "SA", "CO": "SA", "PE": "SA", "UY": "SA", "EC": "SA",
    "BO": "SA", "PY": "SA", "VE": "SA",

    "JP": "AS", "IN": "AS",
    "CN": "AS", "TW": "AS", "HK": "AS", "MO": "AS", "SG": "AS", "KR": "AS",
    "ID": "AS", "MY": "AS", "PH": "AS", "TH": "AS", "VN": "AS", "BD": "AS",
    "PK": "AS", "LK": "AS", "NP": "AS", "IL": "AS", "AE": "AS", "SA": "AS",

    "AU": "OC", "NZ": "OC", "PG": "OC", "FJ": "OC",
    "ZA": "AF", "MA": "AF", "SN": "AF", "DZ": "AF", "TN": "AF", "EG": "AF",
    "NG": "AF", "KE": "AF", "GH": "AF", "CI": "AF", "CM": "AF", "ET": "AF",
}






_LANGUAGE_CONTINENT: dict[str, str] = {
    "en": "EU", "de": "EU", "es": "EU", "fr": "EU", "it": "EU", "nl": "EU",
    "pl": "EU", "pt": "EU", "no": "EU", "nb": "EU", "nn": "EU", "sv": "EU",
    "da": "EU", "fi": "EU", "cs": "EU", "el": "EU", "ro": "EU", "hu": "EU",
    "uk": "EU", "tr": "EU", "ru": "EU",
    "ja": "AS", "zh": "AS", "ko": "AS", "id": "AS", "ms": "AS", "th": "AS",
    "vi": "AS", "hi": "AS", "bn": "AS", "ar": "AS",
}


def _split(name: str) -> tuple[str, str]:
    """``"fr_FR"`` -> ``("fr", "FR")``. Either half may come back empty."""
    parts = str(name or "").replace("-", "_").split("_")
    language = parts[0].lower() if parts and parts[0] else ""
    region = parts[-1].upper() if len(parts) > 1 and len(parts[-1]) == 2 else ""
    return language, region


_cached_locale: tuple[str, str] | None = None


def system_locale() -> tuple[str, str]:
    """``(language, region)`` for this reader, lowercase and uppercase."""













    global _cached_locale
    if _cached_locale is not None:
        return _cached_locale
    chosen = ""
    if QgsSettings is not None:
        try:
            store = QgsSettings()
            if bool(store.value("locale/overrideFlag", False, type=bool)):
                chosen = str(store.value("locale/userLocale", "", type=str) or "")
        except Exception:  # noqa: BLE001 - a missing setting is not a failure
            chosen = ""
    machine = ""
    if QLocale is not None:
        try:
            machine = str(QLocale.system().name() or "")
        except Exception:  # noqa: BLE001 - no locale is not a failure the user sees
            machine = ""
    picked_language, picked_region = _split(chosen)
    machine_language, machine_region = _split(machine)



    _cached_locale = (picked_language or machine_language,
                      picked_region or machine_region)
    return _cached_locale


def _covers(row: dict) -> tuple[str, ...]:
    return tuple(str(c or "").upper() for c in (row.get("also_covers") or []) if c)


def is_home(row: dict, region: str = "") -> bool:
    """Whether the connector is the region's own: its country, or one it also covers."""
    if not region:
        region = system_locale()[1]
    if not region:
        return False
    country = str(row.get("country") or "").upper()
    return region == country or region in _covers(row)


def country_of(row: dict, region: str = "") -> str:
    """The ISO code a connector's tile shows, or "" for a worldwide source."""





    row = row if isinstance(row, dict) else {}
    if not region:
        region = system_locale()[1]
    if region and region in _covers(row):
        return region
    return str(row.get("country") or "").upper()


def distance(row: dict, language: str = "", region: str = "") -> int:
    """Which of the five rings a connector is in: 0 nearest, 4 furthest."""





    row = row if isinstance(row, dict) else {}
    if not language and not region:
        language, region = system_locale()
    country = str(row.get("country") or "").upper()
    if not country:
        return 4
    if is_home(row, region):
        return 0
    speaks = bool(language) and language in [str(x).lower() for x in (row.get("languages") or [])]
    home = _REGION_CONTINENT.get(region or "", "") or _LANGUAGE_CONTINENT.get(language, "")
    near = bool(home) and _REGION_CONTINENT.get(country, "") == home
    if near and speaks:
        return 1
    if near:
        return 2
    if speaks:
        return 3
    return 4


def sort_by_locale(rows: list) -> list:
    """National rows, nearest country first, the server's order within a ring."""





    language, region = system_locale()
    rows = list(rows or [])
    return sorted(rows, key=lambda row: distance(row, language, region))
