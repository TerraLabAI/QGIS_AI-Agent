# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










































from __future__ import annotations

import contextlib
import html
import os
import re
import urllib.parse










VSICURL_ALLOWED_EXTENSIONS = (".parquet", ".geoparquet", ".fgb", ".gpkg", ".pmtiles", ".tif", ".tiff",
                              ".vrt", ".laz", ".las", ".vpc", "{noext}")



STREAMED_PREFIXES = ("/vsicurl", "/vsis3", "/vsigs", "/vsiaz", "/vsioss", "/vsiswift", "/vsihdfs")


def streamed_in_place(layer) -> bool:








    if layer is None:
        return False
    try:
        source = str(layer.source() or "")
    except Exception:  # noqa: BLE001
        return False
    return any(prefix in source for prefix in STREAMED_PREFIXES)


def streamed_size(layer) -> int | None:








    try:
        source = str(layer.source() or "").split("|", 1)[0].strip()
        from osgeo import gdal
    except Exception:  # noqa: BLE001
        return None
    cache_only = getattr(gdal, "VSI_STAT_CACHE_ONLY", None)
    start = min((source.find(prefix) for prefix in STREAMED_PREFIXES if prefix in source), default=-1)
    if start < 0 or cache_only is None or "/vsizip" in source or "/vsitar" in source or "/vsigzip" in source:
        return None
    try:
        stat = gdal.VSIStatL(source[start:], gdal.VSI_STAT_SIZE_FLAG | cache_only)
    except Exception:  # noqa: BLE001
        return None
    return int(stat.size) if stat is not None and stat.size > 0 else None




PERSISTENT_OPTIONS: dict[str, str] = {



    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ",".join(VSICURL_ALLOWED_EXTENSIONS),


    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "CPL_VSIL_CURL_CHUNK_SIZE": "262144",


    "GDAL_HTTP_VERSION": "2",



    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",














    "GDAL_HTTP_CONNECTTIMEOUT": "10",
    "GDAL_HTTP_LOW_SPEED_LIMIT": "1000",
    "GDAL_HTTP_LOW_SPEED_TIME": "30",


    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "25000000",
}







REMOTE_ONLY_OPTIONS = frozenset({"GDAL_DISABLE_READDIR_ON_OPEN"})



SCOPED_OPTIONS: dict[str, str] = {
    **PERSISTENT_OPTIONS,
    "CPL_VSIL_CURL_CHUNK_SIZE": "1048576",
    "GDAL_NUM_THREADS": "ALL_CPUS",






    "CPL_VSIL_CURL_AUTHORIZATION_HEADER_ALLOWED_IF_REDIRECT": "NO",
}


_state = {"applied": False}


def _qgis_extra_cas() -> bytes:













    try:
        from qgis.core import QgsApplication

        manager = QgsApplication.authManager()
        if manager is None:
            return b""
        added = list(manager.databaseCAs()) + list(manager.extraFileCAs())
        if not added:
            return b""


        trusted = {bytes(cert.digest()).hex() for cert in manager.trustedCaCertsCache()}
        extra = [cert for cert in added if bytes(cert.digest()).hex() in trusted]
        return b"".join(bytes(cert.toPem()) for cert in extra)
    except Exception:  # noqa: BLE001
        return b""






CA_KEYS = ("GDAL_CURL_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE")
_OWN_BUNDLES = ("gdal-ca-bundle.pem", "gdal-chain-bundle.pem")


def _base_bundle() -> bytes:







    try:
        from osgeo import gdal



        named = [value for key in CA_KEYS for value in (gdal.GetConfigOption(key), os.environ.get(key))]
    except Exception:  # noqa: BLE001
        named = [os.environ.get(key) for key in CA_KEYS]
    paths = []
    for path in named:
        if path and os.path.basename(path) not in _OWN_BUNDLES and path not in paths and os.path.isfile(path):
            paths.append(path)
    if not paths:
        import sys

        from .host_platform import IS_WINDOWS

        if IS_WINDOWS:


            folder = os.path.dirname(sys.executable)
            system = os.path.join(folder, "curl-ca-bundle.crt")
        else:
            import ssl

            system = ssl.get_default_verify_paths().openssl_cafile
        if system and os.path.isfile(system):
            paths.append(system)
    parts = []
    for path in paths:
        with open(path, "rb") as handle:
            body = handle.read()
        parts.append(body if body.endswith(b"\n") else body + b"\n")
    return b"".join(parts)


def _write_bundle(name: str, parts) -> str | None:

    from .host_platform import retry_file_op
    from .policy import AGENT_HOME

    target = os.path.join(AGENT_HOME, "certs", name)
    os.makedirs(os.path.dirname(target), exist_ok=True)



    temp = target + ".new"
    with open(temp, "wb") as handle:
        for part in parts:
            if part:
                handle.write(part if part.endswith(b"\n") else part + b"\n")
    try:
        retry_file_op(os.replace, temp, target)
    except OSError:


        return target if os.path.exists(target) else None
    return target


def _ca_bundle_with_qgis_extras():












    from .background import on_main_thread, run_on_main_thread


    extra = _qgis_extra_cas() if on_main_thread() else run_on_main_thread(_qgis_extra_cas, timeout=10)
    if not extra:
        return None
    base = _base_bundle()
    if not base:
        from .host_platform import IS_WINDOWS

        if IS_WINDOWS:


            return None
        base = _qgis_trusted_pem() if on_main_thread() else run_on_main_thread(_qgis_trusted_pem, timeout=10)
    if not base or extra in base:
        return None
    return _write_bundle("gdal-ca-bundle.pem", (base, extra))



_CHAIN_PREFIXES = ("/vsicurl/", "/vsicurl_streaming/", "")


def _qgis_trusted_pem() -> bytes:

    try:
        from qgis.core import QgsApplication

        manager = QgsApplication.authManager()
        return bytes(manager.trustedCaCertsPemText()) if manager is not None else b""
    except Exception:  # noqa: BLE001
        return b""


def _chain_bundle():








    from . import missing_roots
    from .background import on_main_thread, run_on_main_thread

    intermediates = missing_roots.intermediates_pem()
    if not intermediates:
        return None
    trusted = _qgis_trusted_pem() if on_main_thread() else run_on_main_thread(_qgis_trusted_pem, timeout=10)
    base = _base_bundle()
    if not base and not trusted:

        return None
    return _write_bundle("gdal-chain-bundle.pem", (base, trusted, intermediates))


def _apply_chain_bundle(gdal, per_path) -> None:
    from . import missing_roots

    if per_path is None:
        return
    bundle = _chain_bundle()
    if not bundle:
        return
    for host in missing_roots.intermediate_hosts():
        for prefix in _CHAIN_PREFIXES:
            for key in CA_KEYS:
                per_path(f"{prefix}https://{host}/", key, bundle)


def apply_persistent() -> bool:

    if _state["applied"]:
        return True
    try:
        from osgeo import gdal
    except ImportError:
        return False
    per_path = getattr(gdal, "SetPathSpecificOption", None)
    for key, value in PERSISTENT_OPTIONS.items():
        if key in REMOTE_ONLY_OPTIONS and per_path is not None:
            for prefix in STREAMED_PREFIXES:
                per_path(prefix, key, value)




            if gdal.GetConfigOption(key) == value:
                gdal.SetConfigOption(key, None)
        else:
            gdal.SetConfigOption(key, value)
    from .background import MainThreadBusy

    try:
        bundle = _ca_bundle_with_qgis_extras()
    except MainThreadBusy:


        return True
    except Exception:  # noqa: BLE001
        bundle = None
    if bundle:


        for key in CA_KEYS:
            gdal.SetConfigOption(key, bundle)
    try:
        _apply_chain_bundle(gdal, per_path)
    except MainThreadBusy:
        return True
    except Exception as exc:  # noqa: BLE001
        from .logger import log_warning

        log_warning(f"Intermediates not handed to GDAL: {exc}")
    _state["applied"] = True
    return True




_PROXY_KEYS = ("GDAL_HTTP_PROXY", "GDAL_HTTP_PROXYUSERPWD")
_PROXY_PREFIXES = STREAMED_PREFIXES
_proxy_state: dict = {"set": None}


def share_proxy(found: dict | None) -> None:













    if os.environ.get("GDAL_HTTP_PROXY") or os.environ.get("GDAL_HTTPS_PROXY"):
        return
    value = None
    if found and found.get("host"):
        port = found.get("port") or 0
        user = str(found.get("user") or "")
        value = (f"{found['host']}{':' + str(port) if port else ''}",
                 f"{user}:{found.get('password') or ''}" if user else None)
    if value == _proxy_state["set"]:
        return
    try:
        from osgeo import gdal
    except ImportError:
        return
    per_path = getattr(gdal, "SetPathSpecificOption", None)
    if per_path is None:

        if value and not gdal.GetConfigOption("GDAL_HTTP_PROXY"):
            gdal.SetConfigOption("GDAL_HTTP_PROXY", value[0])
            if value[1]:
                gdal.SetConfigOption("GDAL_HTTP_PROXYUSERPWD", value[1])
        _proxy_state["set"] = value
        return
    for prefix in _PROXY_PREFIXES:
        for key, part in zip(_PROXY_KEYS, value or (None, None)):
            per_path(prefix, key, part)
    _proxy_state["set"] = value


@contextlib.contextmanager
def scoped_read(gdal=None, **overrides: str):







    if gdal is None:
        try:
            from osgeo import gdal  # type: ignore[no-redef]
        except ImportError:
            yield
            return
    options = {**SCOPED_OPTIONS, **overrides}
    thread_local = getattr(gdal, "config_options", None)
    if thread_local is not None:
        with thread_local(options):
            yield
        return
    previous = {key: gdal.GetConfigOption(key) for key in options}
    for key, value in options.items():
        gdal.SetConfigOption(key, value)
    try:
        yield
    finally:
        for key, value in previous.items():
            gdal.SetConfigOption(key, value)










_XML_DATASET_MAX_BYTES = 2 * 1024 * 1024
_XML_SOURCE_RE = re.compile(
    rb"(?is)<(SourceFilename|SrcDataSource|SourceDataset)\b([^>]*)>(.*?)</\1\s*>")


def looks_like_xml(head: bytes) -> bool:
    return bytes(head or b"").lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")


def xml_dataset_refusal(body: bytes, base_url: str) -> str | None:

    if not looks_like_xml(body):
        return None
    from . import net

    for _tag, attrs, raw in _XML_SOURCE_RE.findall(body):
        text = html.unescape(raw.decode("utf-8", "replace")).strip()
        if text.lower().startswith("/vsicurl/"):
            text = text[len("/vsicurl/"):]
        elif b'relativetovrt="1"' in attrs.lower().replace(b" ", b"") and "://" not in text \
                and not text.startswith(("/", "\\")):
            text = urllib.parse.urljoin(base_url, text)
        if not text.lower().startswith(("http://", "https://")):
            return f"the dataset at {base_url} reads a source this plugin does not open ({text[:120]})"
        try:
            net.check_url(text)
        except Exception as exc:  # noqa: BLE001
            return f"the dataset at {base_url} reads {text[:120]}, which is not fetched: {exc}"
    return None


def remote_dataset_refusal(url: str) -> str | None:







    from . import net, security_urls

    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    ext = os.path.splitext(parts.path)[1].lower()
    if ext not in ("", ".vrt") or security_urls._routed_public_host(parts.hostname or ""):
        return None
    try:
        response = net.open_url(url, timeout=15)
    except Exception:  # noqa: BLE001
        return None
    try:
        reader = getattr(response, "read1", None) or response.read
        head = reader(256)
        if not looks_like_xml(head):
            return None
        try:
            rest = net.read_bounded(response, _XML_DATASET_MAX_BYTES, deadline=None)
        except net.FetchTooLarge:
            return f"the XML dataset at {url} is too large to check its sources"
        except Exception:  # noqa: BLE001
            return None
        return xml_dataset_refusal(head + rest, url)
    finally:
        with contextlib.suppress(Exception):
            response.close()
