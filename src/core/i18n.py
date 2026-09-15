# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Internationalization (i18n) support for AI Agent by TerraLab."""




















from __future__ import annotations

import os
import struct
import tempfile
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


def lookup(context: str, source_text: str) -> str | None:
    """The answer the installed translator gives for one string, or None."""









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






_QM_MAGIC = bytes((0x3C, 0xB8, 0x64, 0x18, 0xCA, 0xEF, 0x9C, 0x95,
                   0xCD, 0x21, 0x1C, 0xBF, 0x60, 0xA1, 0xBD, 0xDD))
_QM_HASHES, _QM_MESSAGES = 0x42, 0x69
_TAG_END, _TAG_TRANSLATION, _TAG_SOURCE, _TAG_CONTEXT = 1, 3, 6, 7


def _elf_hash(data: bytes) -> int:
    h = 0
    for byte in data:
        h = ((h << 4) + byte) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g & 0xFFFFFFFF
    return h or 1


def build_qm(translations: dict[str, dict[str, str]]) -> bytes:
    """A .qm image answering every lookup the way ``lookup`` does."""









    messages = bytearray()
    index: list[tuple[int, int]] = []

    def add(context: bytes | None, source: bytes, text: str) -> None:
        encoded = text.encode("utf-16-be")
        offset = len(messages)
        messages.append(_TAG_TRANSLATION)
        messages.extend(struct.pack(">I", len(encoded)))
        messages.extend(encoded)
        if context is not None:
            messages.append(_TAG_CONTEXT)
            messages.extend(struct.pack(">I", len(context)))
            messages.extend(context)
        messages.append(_TAG_SOURCE)
        messages.extend(struct.pack(">I", len(source)))
        messages.extend(source)
        messages.append(_TAG_END)
        index.append((_elf_hash(source), offset))

    fallback: dict[str, str] = {}
    for context_name in sorted(translations):
        context = context_name.encode("utf-8")
        for source_text, text in translations[context_name].items():
            if not source_text or not text:
                continue
            add(context, source_text.encode("utf-8"), text)
            fallback.setdefault(source_text, text)
    for source_text, text in fallback.items():
        add(None, source_text.encode("utf-8"), text)

    hashes = b"".join(struct.pack(">II", h, offset) for h, offset in sorted(index))
    return (_QM_MAGIC
            + bytes((_QM_HASHES,)) + struct.pack(">I", len(hashes)) + hashes
            + bytes((_QM_MESSAGES,)) + struct.pack(">I", len(messages)) + bytes(messages))














_WARM_CONTEXT = "AIAgentI18n"
_translator: QTranslator | None = None


def _warm_up() -> None:
    QCoreApplication.translate(_WARM_CONTEXT, "warm up")


def _load_qm(image: bytes) -> QTranslator | None:
    """A QTranslator holding ``image``."""

    fd, path = tempfile.mkstemp(prefix="ai_agent_", suffix=".qm")
    translator = QTranslator()
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(image)
        loaded = translator.load(path)
    finally:
        try:
            os.remove(path)
        except OSError as exc:
            _log_warning(f"Translation image not removed: {exc}")
    return translator if loaded else None


def _log_warning(text: str) -> None:
    try:
        from qgis.core import Qgis, QgsMessageLog

        from .log_scrub import scrub_sensitive

        QgsMessageLog.logMessage(scrub_sensitive(text), "AI Agent", level=Qgis.MessageLevel.Warning)
    except Exception:  # nosec B110 - no message log to write to
        return


def install() -> None:
    """Install the plugin's translations on the running Qt application."""






    global _translator
    if _translator is not None:
        return
    app = QCoreApplication.instance()
    if app is None:
        return
    _load_translations()
    if not _translations:
        return
    try:
        translator = _load_qm(build_qm(_translations))
    except Exception as exc:  # noqa: BLE001 - English is always a valid answer
        _log_warning(f"Translations not installed: {exc}")
        return
    if translator is None:
        _log_warning("Translations not installed: Qt refused the translation image.")
        return
    _translator = translator
    app.installTranslator(translator)
    _warm_up()


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
