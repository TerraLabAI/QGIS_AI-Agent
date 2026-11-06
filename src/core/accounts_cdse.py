# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

import time

from qgis.core import QgsApplication

from . import accounts, accounts_oauth, background
from .logger import log_warning

PROVIDER = "copernicus"

NAME = "Copernicus"
CLIENT = accounts_oauth.Client("https://identity.dataspace.copernicus.eu/auth/realms/CDSE",
                               "sh-b1c3a958-52d4-40fe-a333-153595d1c71e", "openid offline_access")
KEYS_URL = "https://s3-keys-manager.cloudferro.com/api/user/credentials"

PROPAGATE_S = 5
BROWSER_VIA = "browser"


def available() -> bool:

    try:
        manager = QgsApplication.authManager()
        return bool(manager is not None and not manager.isDisabled() and "Basic" in manager.authMethodsKeys())
    except Exception:  # noqa: BLE001
        return False


def _mint(tokens: dict, wait=time.sleep) -> tuple[dict, str]:

    access = str(tokens.get("access_token") or "")
    status, body = accounts_oauth.http(KEYS_URL, {}, {"Authorization": f"Bearer {access}"})
    access_id, secret = str(body.get("access_id") or ""), str(body.get("secret") or "")
    if status not in (200, 201) or not access_id or not secret:
        return {}, "network" if status is None or status >= 500 else "keys"
    wait(PROPAGATE_S)
    return {"access_id": access_id, "secret": secret, "access_token": access,
            "refresh_token": str(tokens.get("refresh_token") or "")}, ""


def sign_in() -> accounts_oauth.DeviceSignIn:

    def after(tokens: dict) -> tuple[dict, str]:
        return _mint(tokens)
    return accounts_oauth.DeviceSignIn(CLIENT, after, "Copernicus Data Space sign-in")


def save(result: dict, fresh: bool = True) -> str:




    result = dict(result or {})
    entry = accounts.entries().get(PROVIDER) or {}
    previous = accounts.vault_values(PROVIDER) if entry.get("via") == BROWSER_VIA else {}

    account = accounts.token_facts(result.get("access_token", "")).get("account") or entry.get("account")
    key = accounts.store_s3_keys(PROVIDER, result.get("access_id", ""), result.get("secret", ""),
                                 extra={"refresh_token": result.get("refresh_token", "")}, via=BROWSER_VIA,
                                 facts={"account": account} if account else None)
    old_id = str(previous.get("username") or "")
    if key and previous and fresh:
        disconnect(previous)
    elif key and old_id and old_id != result.get("access_id") and result.get("access_token"):
        _delete_keys(result["access_token"], old_id)
    previous.clear()
    result.clear()
    return key


def discard(result: dict) -> None:

    disconnect({"refresh_token": (result or {}).get("refresh_token"), "username": (result or {}).get("access_id")})


def _keep_token(refresh_token: str) -> None:

    values = accounts.vault_values(PROVIDER)
    account = (accounts.entries().get(PROVIDER) or {}).get("account")
    if values.get("username") and values.get("password"):
        accounts.store_s3_keys(PROVIDER, values["username"], values["password"],
                               extra={"refresh_token": refresh_token}, via=BROWSER_VIA,
                               facts={"account": account} if account else None)
    values.clear()


def renew_from_worker() -> bool:


    if background.on_main_thread():
        return False
    entry = accounts.entries().get(PROVIDER) or {}
    if entry.get("via") != BROWSER_VIA:
        return False
    try:
        values = background.run_on_main_thread(accounts.vault_values, PROVIDER) or {}
    except Exception:  # noqa: BLE001
        return False
    refresh_token = str(values.get("refresh_token") or "")
    values.clear()
    if not refresh_token:
        return False
    tokens, why = accounts_oauth.refresh(CLIENT, refresh_token)
    if not tokens:
        log_warning(f"Copernicus Data Space: the saved sign-in was not renewed ({why})")
        return False
    if not tokens.get("refresh_token"):
        tokens["refresh_token"] = refresh_token
    result, why = _mint(tokens)
    try:
        if not result:
            log_warning(f"Copernicus Data Space: no new S3 keys ({why})")
            if tokens["refresh_token"] != refresh_token:
                background.run_on_main_thread(_keep_token, tokens["refresh_token"], timeout=20)
            return False
        return bool(background.run_on_main_thread(save, result, False, timeout=20))
    except Exception:  # noqa: BLE001
        return False
    finally:
        tokens.clear()
        result.clear()


def _delete_keys(access_token: str, access_id: str, on_done=None) -> None:
    accounts_oauth.send("DELETE", f"{KEYS_URL}/access_id/{access_id}", None,
                        {"Authorization": f"Bearer {access_token}"}, on_done)


def disconnect(values: dict) -> None:




    refresh_token = str((values or {}).get("refresh_token") or "")
    access_id = str((values or {}).get("username") or "")
    if not refresh_token:
        return

    def refreshed(status, body) -> None:
        access = str(body.get("access_token") or "") if status == 200 else ""
        newest = str(body.get("refresh_token") or refresh_token) if status == 200 else refresh_token
        if access and access_id:
            _delete_keys(access, access_id, lambda *_: accounts_oauth.logout(CLIENT, newest))
        else:
            accounts_oauth.logout(CLIENT, newest)

    accounts_oauth.send("POST", CLIENT.token_url, {"grant_type": "refresh_token", "client_id": CLIENT.client_id,
                                                   "refresh_token": refresh_token}, None, refreshed)
