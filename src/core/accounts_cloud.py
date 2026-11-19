# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





















from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit

from . import accounts, vsi
from .tool_registry import tool_error

S3, AZURE, GCS = "s3", "azure", "gcs"
LABELS = {S3: "Amazon S3", AZURE: "Azure Blob Storage", GCS: "Google Cloud Storage"}
_VSI = {S3: "/vsis3", AZURE: "/vsiaz", GCS: "/vsigs"}
_PREFIX = "cloud:"
_READ_ONLY_SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"

REFUSED = re.compile(r"AccessDenied|InvalidAccessKeyId|SignatureDoesNotMatch|AuthenticationFailed|"
                     r"AuthorizationFailure|AuthorizationPermissionMismatch|InvalidAuthenticationInfo|"
                     r"InvalidCredentials|invalid_grant|\b40[13]\b")


_SIGNED = {"sig", "x-amz-signature", "x-goog-signature", "signature", "awsaccesskeyid", "googleaccessid"}
_REGION = r"[a-z]{2}(?:-gov)?-[a-z]+-\d"
_AWS = re.compile(rf"^(?:(?P<bucket>[a-z0-9][a-z0-9.\-]*)\.)?s3(?:\.dualstack)?"
                  rf"(?:[.-](?P<region>{_REGION}))?\.amazonaws\.com$")

_COMPATIBLE = (
    (re.compile(r"s3\.(?P<region>[a-z0-9-]+)\.scw\.cloud"), "Scaleway"),
    (re.compile(r"s3\.(?P<region>[a-z0-9-]+)\.(?:io\.|perf\.)?cloud\.ovh\.net"), "OVHcloud"),
    (re.compile(r"s3\.(?:(?P<region>[a-z0-9-]+)\.)?wasabisys\.com"), "Wasabi"),
    (re.compile(r"[a-f0-9]{32}\.(?:eu\.)?r2\.cloudflarestorage\.com"), "Cloudflare R2"),
    (re.compile(r"(?P<region>[a-z0-9-]+)\.digitaloceanspaces\.com"), "DigitalOcean Spaces"),
)
_AZURE_HOST = re.compile(r"^(?P<account>[a-z0-9]{3,24})\.(?:blob|dfs)\.core\.windows\.net$")
_GCS_HOSTS = ("storage.googleapis.com", "storage.cloud.google.com")
_VSI_PATH = re.compile(r"^/vsi(?P<handler>s3|gs|az|adls)(?:_streaming)?/(?P<bucket>[^/]+)/(?P<key>.+)$")
_VSI_STORE = {"s3": S3, "gs": GCS, "az": AZURE, "adls": AZURE}

_PROBE_HTTP = {"GDAL_HTTP_CONNECTTIMEOUT": "10", "GDAL_HTTP_TIMEOUT": "20", "GDAL_HTTP_MAX_RETRY": "0"}


@dataclass(frozen=True)
class Ref:
    store: str
    bucket: str
    key: str
    account: str = ""
    endpoint: str = ""
    https: bool = True
    region: str = ""
    located: bool = False
    address: str = ""
    vendor: str = ""

    @property
    def path(self) -> str:
        return f"{_VSI[self.store]}/{self.bucket}/{self.key}"

    @property
    def route_path(self) -> str:

        return f"{_VSI[self.store]}/{self.bucket}"

    @property
    def shown(self) -> str:

        where = f"{self.account}/{self.bucket}" if self.account else self.bucket
        return f"{self.vendor or LABELS[self.store]} · {where}"


def _signed(query: str) -> bool:
    return any(name.lower() in _SIGNED for name, _ in parse_qsl(query, keep_blank_values=True))


def _split(path: str) -> tuple[str, str]:
    bucket, _, key = unquote(path).lstrip("/").partition("/")
    return bucket, key


def parse(text) -> Ref | None:


    raw = str(text or "").strip()
    vsi = _VSI_PATH.match(raw)
    if vsi:
        if vsi["handler"] == "s3" and vsi["bucket"] == accounts._CDSE_BUCKET:
            return None
        ref = Ref(_VSI_STORE[vsi["handler"]], vsi["bucket"], vsi["key"], address=raw)
        return None if ref.key.endswith("/") else ref
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if _signed(parts.query) or parts.password or (parts.username and scheme not in ("abfs", "abfss")):
        return None
    address = raw.split("?", 1)[0].split("#", 1)[0]
    ref = None
    if scheme in ("s3", "gs"):
        bucket, _, key = raw.split("?", 1)[0][len(scheme) + 3:].partition("/")
        if scheme == "s3" and (accounts.cdse_key(raw) or bucket == accounts._CDSE_BUCKET):
            return None
        ref = Ref(S3 if scheme == "s3" else GCS, bucket, key, address=address)
    elif scheme in ("az", "abfs", "abfss"):
        host = (parts.hostname or "").lower()
        if "@" in parts.netloc:
            container, account = parts.netloc.split("@", 1)[0], host.split(".", 1)[0]
            ref = Ref(AZURE, container, unquote(parts.path).lstrip("/"), account=account, located=True,
                      address=address)
        elif _AZURE_HOST.match(host):
            bucket, key = _split(parts.path)
            ref = Ref(AZURE, bucket, key, account=_AZURE_HOST.match(host)["account"], located=True, address=address)
        else:
            bucket, _, key = raw.split("?", 1)[0][len(scheme) + 3:].partition("/")
            ref = Ref(AZURE, bucket, key, address=address)
    elif scheme in ("http", "https"):
        ref = _https(parts, address)
    if ref is None or not ref.bucket or not ref.key or ref.key.endswith("/"):
        return None
    return ref


def _https(parts, address: str) -> Ref | None:
    host = (parts.hostname or "").lower().rstrip(".")
    secure = parts.scheme.lower() == "https"
    aws = _AWS.match(host)
    if aws:
        if aws["bucket"]:
            return Ref(S3, aws["bucket"], unquote(parts.path).lstrip("/"), region=aws["region"] or "",
                       located=True, address=address)
        bucket, key = _split(parts.path)
        return Ref(S3, bucket, key, region=aws["region"] or "", located=True, address=address)
    azure = _AZURE_HOST.match(host)
    if azure:
        bucket, key = _split(parts.path)
        return Ref(AZURE, bucket, key, account=azure["account"], located=True, address=address)
    if host in _GCS_HOSTS:
        bucket, key = _split(parts.path)
        return Ref(GCS, bucket, key, located=True, address=address)
    if host.endswith(".storage.googleapis.com"):
        return Ref(GCS, host[: -len(".storage.googleapis.com")], unquote(parts.path).lstrip("/"),
                   located=True, address=address)
    endpoint = host + (f":{parts.port}" if parts.port else "")
    for pattern, vendor in _COMPATIBLE:
        found = pattern.fullmatch(host)
        if found:
            bucket, key = _split(parts.path)
            return Ref(S3, bucket, key, endpoint=endpoint, https=secure, region=_region(found),
                       located=True, address=address, vendor=vendor)
        head, _, rest = host.partition(".")
        found = pattern.fullmatch(rest)
        if head and found:
            return Ref(S3, head, unquote(parts.path).lstrip("/"), endpoint=rest, https=secure,
                       region=_region(found), located=True, address=address, vendor=vendor)

    for entry in _stores():
        if entry.get("store") == S3 and entry.get("endpoint") == endpoint:
            bucket, key = _split(parts.path)
            if bucket == entry.get("bucket"):
                return Ref(S3, bucket, key, endpoint=endpoint, https=secure, region=str(entry.get("region") or ""),
                           located=True, address=address)
    return None


def _region(found) -> str:
    region = found.groupdict().get("region") or ""
    return "auto" if "r2.cloudflarestorage" in found.string else region




def _stores() -> list[dict]:
    return [dict(entry, key=key) for key, entry in accounts.entries().items()
            if key.startswith(_PREFIX) and entry.get("kind") == accounts.CLOUD]


def find(ref: Ref) -> tuple[str, dict] | tuple[str, None]:

    for entry in _stores():
        if entry.get("store") != ref.store:
            continue
        if ref.store == AZURE:
            if ref.account and entry.get("account") != ref.account:
                continue
            if entry.get("secret") == "sas" and entry.get("bucket") != ref.bucket:
                continue
            if entry.get("secret") != "sas" and not ref.account and entry.get("bucket") != ref.bucket:
                continue
        elif entry.get("bucket") != ref.bucket:
            continue
        if ref.store == S3 and ref.located and str(entry.get("endpoint") or "") != ref.endpoint:
            continue
        return entry["key"], entry
    key = key_for(ref)
    taken = accounts.entries().get(key)
    if taken and ref.store == S3 and str(taken.get("endpoint") or "") != ref.endpoint:

        key = f"{_PREFIX}{S3}:{ref.bucket}@{ref.endpoint or 's3.amazonaws.com'}"
    return key, None


def key_for(ref: Ref) -> str:



    if ref.store == AZURE:
        return f"{_PREFIX}{AZURE}:{ref.account}/{ref.bucket}"
    where = f"@{ref.endpoint}" if ref.store == S3 and ref.endpoint else ""
    return f"{_PREFIX}{ref.store}:{ref.bucket}{where}"


def save(key: str, store: str, values: dict, meta: dict, facts: dict | None = None) -> str:


    if not key.startswith(f"{_PREFIX}{store}:"):
        return ""
    label = LABELS.get(store, store)
    account, bucket = str(meta.get("account") or ""), str(meta.get("bucket") or "")
    shown = f"{account}/{bucket}" if account and bucket else (account or bucket)
    host = str(meta.get("endpoint") or "") or _service_host(store, str(meta.get("account") or ""))
    kept = {name: meta[name] for name in ("bucket", "account", "endpoint", "region", "https", "secret", "vendor",
                                          "address") if name in meta}
    return accounts.store(f"{store}://{shown}", accounts.CLOUD, values, f"{meta.get('vendor') or label} · {shown}",
                          key=key, facts=facts, meta=dict(kept, store=store, host=host))




def need(ref: Ref, kind: str, failure: dict, key: str = "", what: str = "") -> dict:

    key = key or find(ref)[0]
    entry = accounts.entries().get(key) or {}
    if kind == "retry" and entry:
        accounts.refused_store(key)
    return {accounts.MARK: {
        "kind": kind, "key": key, "host": ref.shown, "label": entry.get("label") or ref.shown,
        "store": ref.store, "address": ref.address, "bucket": ref.bucket, "account": ref.account,
        "endpoint": ref.endpoint, "region": ref.region, "located": ref.located, "https": ref.https,
        "vendor": ref.vendor, "what": str(what or "")[:200],
        "expired": kind == "retry" and accounts.expired(entry)},
        "failure": failure}


def consent(ref: Ref, key: str) -> dict:
    asked = need(ref, "consent", tool_error("The user did not allow their saved access to this storage.",
                                            "PERMISSION_DENIED", hint="account_consent_denied", host=ref.shown),
                 key, what=f"Open {ref.address}.")
    asked[accounts.MARK]["details"] = details(ref, key)
    return asked


def _service_host(store: str, account: str = "") -> str:
    return {S3: "s3.amazonaws.com", GCS: "storage.googleapis.com"}.get(store, f"{account}.blob.core.windows.net")


def details(ref: Ref, key: str) -> dict:

    entry = accounts.entries().get(key) or {}
    where = str(entry.get("endpoint") or "") or _service_host(ref.store, str(entry.get("account") or ref.account))
    return {"file": ref.address, "sent_to": where, "access": str(entry.get("label") or ref.shown), "read_only": True}




def _options(ref: Ref, entry: dict) -> tuple[dict, dict, dict]:




    if ref.store == S3:
        endpoint = str(entry.get("endpoint") or "") or "s3.amazonaws.com"
        aws = endpoint == "s3.amazonaws.com"
        route = {"AWS_S3_ENDPOINT": endpoint,
                 "AWS_REGION": str(entry.get("region") or ref.region or ("auto" if "r2.cloudflarestorage" in endpoint
                                                                          else "us-east-1")),
                 "AWS_VIRTUAL_HOSTING": "TRUE" if aws and "." not in ref.bucket else "FALSE",
                 "AWS_HTTPS": "NO" if entry.get("https") is False else "YES",
                 "AWS_NO_SIGN_REQUEST": "YES"}
        signed = {"AWS_ACCESS_KEY_ID": "username", "AWS_SECRET_ACCESS_KEY": "password"}  # nosec B105
        return route, signed, {"AWS_NO_SIGN_REQUEST": "NO"}
    if ref.store == AZURE:
        route = {"AZURE_STORAGE_ACCOUNT": str(entry.get("account") or ref.account), "AZURE_NO_SIGN_REQUEST": "YES"}
        secret = ("AZURE_STORAGE_SAS_TOKEN" if entry.get("secret") == "sas"
                  else "AZURE_STORAGE_ACCESS_KEY")  # pragma: allowlist secret
        return route, {secret: "password"}, {"AZURE_NO_SIGN_REQUEST": "NO"}  # pragma: allowlist secret
    if entry.get("secret") == "hmac":
        signed = {"GS_ACCESS_KEY_ID": "username", "GS_SECRET_ACCESS_KEY": "password"}  # nosec B105
        return {"GS_NO_SIGN_REQUEST": "YES"}, signed, {"GS_NO_SIGN_REQUEST": "NO"}

    signed = {"GS_OAUTH2_CLIENT_EMAIL": "username", "GS_OAUTH2_PRIVATE_KEY": "password"}  # pragma: allowlist secret
    return {"GS_NO_SIGN_REQUEST": "YES"}, signed, {"GS_NO_SIGN_REQUEST": "NO", "GS_OAUTH2_SCOPE": _READ_ONLY_SCOPE}


def give(ref: Ref, key: str) -> bool:


    route, vault, literal = _options(ref, accounts.entries().get(key) or {})
    return accounts.give_gdal(key, ref.route_path, route, ref.path, vault, literal, _fence(ref, literal))


def take_back(ref: Ref, key: str) -> None:


    route, vault, literal = _options(ref, accounts.entries().get(key) or {})
    accounts.take_back_gdal(key, [ref.path, *_fence(ref, literal)])


def _fence(ref: Ref, literal: dict) -> dict:
    no_sign = next(option for option in literal if option.endswith("NO_SIGN_REQUEST"))
    return {ref.path + chr(code): {no_sign: "YES"} for code in range(1, 128)}


def probe(ref: Ref, values: dict, meta: dict) -> str:



    from osgeo import gdal
    route, vault, literal = _options(ref, meta)
    accounts.free_route(ref.route_path)
    options = {**literal, **{option: str(values.get(field) or "") for option, field in vault.items()}}
    said: list[str] = []
    gdal.PushErrorHandler(lambda _cls, _no, msg: said.append(str(msg)))
    debug = vsi.thread_options(gdal, CPL_DEBUG="ON", **_PROBE_HTTP)
    debug.__enter__()
    try:
        for prefix in (ref.route_path, ref.path):
            for option, value in route.items():
                gdal.SetPathSpecificOption(prefix, option, value)
        for option, value in options.items():
            gdal.SetPathSpecificOption(ref.path, option, value)

        gdal.VSICurlClearCache()
        found = gdal.VSIStatL(ref.path)
    finally:
        debug.__exit__(None, None, None)
        gdal.PopErrorHandler()
        accounts.clear_gdal_paths([ref.route_path, ref.path])
        gdal.VSICurlPartialClearCache(ref.path)
    return "refused" if found is None and any(REFUSED.search(line) for line in said) else ""


def located(ref: Ref, key: str) -> Ref:


    entry = accounts.entries().get(key) or {}
    if ref.store == AZURE and not ref.account and entry.get("account"):
        return Ref(AZURE, ref.bucket, ref.key, account=str(entry["account"]), located=True, address=ref.address)
    return ref
