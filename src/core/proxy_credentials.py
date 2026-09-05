# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The proxy user name and password a user configured in QGIS."""















from __future__ import annotations

from .logger import log_warning


def qgis_proxy_credentials() -> tuple[str, str]:
    """Return (user, password), each empty when QGIS has none to give."""














    try:
        from qgis.core import QgsSettings

        settings = QgsSettings()
        if not settings.value("proxy/proxyEnabled", False, type=bool):
            return "", ""
        authcfg = str(settings.value("proxy/authcfg", "", type=str) or "")
        if authcfg:
            user, password = _credentials_from_auth_config(authcfg)
            if user:
                return user, password
        user = str(settings.value("proxy/proxyUser", "", type=str) or "")
        password = str(settings.value("proxy/proxyPassword", "", type=str) or "")
        return user, password
    except Exception as err:  # noqa: BLE001 - absent credentials, not a crash
        log_warning(f"Reading the QGIS proxy credentials failed: {type(err).__name__}")
        return "", ""


def _credentials_from_auth_config(authcfg: str) -> tuple[str, str]:
    """Read the pair out of a QGIS authentication configuration."""




    from qgis.core import QgsApplication, QgsAuthMethodConfig

    auth_mgr = QgsApplication.authManager()
    if auth_mgr is None or not auth_mgr.masterPasswordIsSet():
        return "", ""
    config = QgsAuthMethodConfig()



    loaded = auth_mgr.loadAuthenticationConfig(authcfg, config, True)
    if isinstance(loaded, tuple):
        ok, config = (loaded + (config,))[:2]
    else:
        ok = loaded
    if not ok or config is None:
        return "", ""
    return config.config("username", "") or "", config.config("password", "") or ""
