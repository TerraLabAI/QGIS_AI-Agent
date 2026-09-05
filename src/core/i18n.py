# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Internationalization (i18n) support for AI Agent by TerraLab."""




















from __future__ import annotations

import os
import xml.etree.ElementTree as ET  # nosec B405

try:
    from defusedxml.ElementTree import parse as _safe_parse
except ImportError:
    _safe_parse = ET.parse

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QCoreApplication, QTranslator





_translations: dict[str, dict[str, str]] = {}
_context_order: list = []


_loaded = False


LANGUAGE_FALLBACKS = {
    "pt": "pt_BR",
    "pt_PT": "pt_BR",
    "es_MX": "es",
    "es_AR": "es",



    "zh": "zh_CN",
    "zh_Hans": "zh_CN",
    "zh_SG": "zh_CN",
    "zh_Hant": "zh_TW",
    "zh_HK": "zh_TW",
    "zh_MO": "zh_TW",
}


def locale_variants(locale: str) -> list[str]:
    """Ordered language codes to try for a locale string."""




    variants: list[str] = []
    normalized = (locale or "").replace("-", "_")
    if not normalized:
        return variants
    parts = normalized.split("_")
    lang = parts[0]
    if "_" in normalized:
        variants.append(normalized)
        if len(parts) >= 3:



            lang_script = f"{parts[0]}_{parts[1]}"
            variants.append(lang_script)
            if lang_script in LANGUAGE_FALLBACKS:
                variants.append(LANGUAGE_FALLBACKS[lang_script])
        variants.append(lang)
        if normalized in LANGUAGE_FALLBACKS:
            variants.append(LANGUAGE_FALLBACKS[normalized])
        if lang in LANGUAGE_FALLBACKS:
            variants.append(LANGUAGE_FALLBACKS[lang])
    else:
        variants.append(lang)
        if lang in LANGUAGE_FALLBACKS:
            variants.append(LANGUAGE_FALLBACKS[lang])
    return variants







_user_locale: str | None = None


def current_locale() -> str:
    """The plugin's configured locale, or the QGIS UI locale as its fallback."""







    global _user_locale
    if _user_locale is None:
        try:
            from .settings import Settings

            _user_locale = str(Settings().locale or "")
        except Exception:  # noqa: BLE001 -- locale is best-effort  # nosec B110
            try:
                _user_locale = str(QgsSettings().value("locale/userLocale", "en_US") or "")
            except Exception:  # noqa: BLE001
                return ""
    return _user_locale


def reset_locale_cache() -> None:
    """Drop the memoized locale and everything loaded from it."""





    global _user_locale, _loaded
    _user_locale = None
    _loaded = False
    _translations.clear()


def resolve_language(supported) -> str | None:
    """The first ``supported`` language code matching the current locale."""



    try:
        for variant in locale_variants(current_locale()):
            if variant in supported:
                return variant
    except Exception:  # noqa: BLE001 -- locale is best-effort  # nosec B110
        return None
    return None


def _load_translations() -> None:
    """Load translations from the .ts XML file matching the current locale."""





    global _loaded

    if _loaded:
        return





    try:
        _load_translations_body()
    finally:
        _loaded = True


def _load_translations_body() -> None:
    ts_path = None
    try:
        locale = current_locale()
        if not locale:
            return


        if locale.startswith("en"):
            return


        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        for variant in locale_variants(locale):
            candidate = os.path.join(plugin_dir, "i18n", f"ai_agent_{variant}.ts")
            if os.path.exists(candidate):
                ts_path = candidate
                break

        if ts_path is None:
            return

        tree = _safe_parse(ts_path)
        root = tree.getroot()




        parsed: dict[str, dict[str, str]] = {}

        for context in root.findall("context"):
            name_el = context.find("name")
            context_name = name_el.text if name_el is not None else ""
            bucket = parsed.setdefault(context_name or "", {})

            for message in context.findall("message"):
                source = message.find("source")
                translation = message.find("translation")

                if source is None or translation is None:
                    continue

                source_text = source.text or ""



                translation_text = translation.text
                if translation_text is None:
                    forms = translation.findall("numerusform")
                    if forms:
                        translation_text = forms[0].text or ""
                    else:
                        translation_text = "".join(translation.itertext())


                if translation_text and translation.get("type") != "unfinished":
                    bucket[source_text] = translation_text

        _translations.update(parsed)


        _context_order[:] = sorted(_translations)

    except Exception as e:  # noqa: BLE001 -- English is always a valid answer
        try:
            from qgis.core import Qgis, QgsMessageLog

            from .log_scrub import scrub_sensitive

            QgsMessageLog.logMessage(
                scrub_sensitive(f"Failed to load translations from {ts_path}: {e}"),
                "AI Agent",
                level=Qgis.MessageLevel.Warning,
            )
        except Exception:  # nosec B110 - translator may be gone
            pass  # nosec B110


class TsTranslator(QTranslator):
    """Qt translator backed by the .ts table above, instead of a .qm file."""








    def translate(self, context, source_text, disambiguation=None, n=-1):  # noqa: ARG002
        if not _loaded:
            _load_translations()
        bucket = _translations.get(context)
        if bucket:
            found = bucket.get(source_text)
            if found:
                return found










        for context_name in _context_order or sorted(_translations):
            found = _translations.get(context_name, {}).get(source_text)
            if found:
                return found
        return None


_translator: TsTranslator | None = None


def install() -> None:
    """Install the .ts-backed translator on the running Qt application."""





    global _translator
    if _translator is not None:
        return
    app = QCoreApplication.instance()
    if app is None:
        return
    _translator = TsTranslator()
    app.installTranslator(_translator)
    _load_translations()


def uninstall() -> None:
    """Remove the translator installed by install(). Call from unload()."""
    global _translator
    if _translator is None:
        return
    app = QCoreApplication.instance()
    if app is not None:
        try:
            app.removeTranslator(_translator)
        except Exception:  # nosec B110 - translator may be gone
            pass
    _translator = None
