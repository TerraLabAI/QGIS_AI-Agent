# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










































from __future__ import annotations

import contextlib
import html
import os
import posixpath
import re
import urllib.parse

from . import machine, net, security_urls
from .background import MainThreadBusy, on_main_thread, run_on_main_thread
from .host_platform import retry_file_op
from .policy import AGENT_HOME











VSICURL_ALLOWED_EXTENSIONS = (".parquet", ".geoparquet", ".fgb", ".gpkg", ".pmtiles", ".tif", ".tiff",
                              ".jp2", ".vrt", ".laz", ".las", ".vpc", "{noext}")



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





_THREADED_GTIFF_FIXED = 3070100


def _without_broken_threads(gdal, options: dict) -> dict:

    if "GDAL_NUM_THREADS" not in options:
        return options
    try:
        if int(gdal.VersionInfo("VERSION_NUM")) >= _THREADED_GTIFF_FIXED:
            return options
    except (AttributeError, TypeError, ValueError):
        pass
    return {key: value for key, value in options.items() if key != "GDAL_NUM_THREADS"}


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


_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"
_PEM_BLOCK = re.compile(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL)


def _windows_store_cas(base: bytes) -> bytes:











    import ssl

    held = set()
    for block in _PEM_BLOCK.findall(base):
        try:
            held.add(ssl.PEM_cert_to_DER_cert(block.decode("ascii")))
        except ValueError:
            continue
    parts = []
    try:
        for store in ("CA", "ROOT"):
            for der, encoding, trust in ssl.enum_certificates(store):
                if encoding == "x509_asn" and (trust is True or _SERVER_AUTH in trust) and der not in held:
                    held.add(der)
                    parts.append(ssl.DER_cert_to_PEM_cert(der).encode("ascii"))
    except OSError as exc:
        from .logger import log_warning

        log_warning(f"Windows certificate store not read for GDAL: {exc}")
        return b""
    return b"".join(parts)


def _ca_bundle_with_qgis_extras():














    from .host_platform import IS_WINDOWS


    extra = _qgis_extra_cas() if on_main_thread() else run_on_main_thread(_qgis_extra_cas, timeout=10)
    if not extra and not IS_WINDOWS:
        return None
    base = _base_bundle()
    if IS_WINDOWS:
        if not base:


            return None
        extra += _windows_store_cas(base + extra)
    elif not base:
        base = _qgis_trusted_pem() if on_main_thread() else run_on_main_thread(_qgis_trusted_pem, timeout=10)
    if not extra or not base or extra in base:
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









BLOCK_CACHE_SHARE = 32
BLOCK_CACHE_MIN_MB = 128
BLOCK_CACHE_MAX_MB = 512


def size_block_cache() -> None:





    try:
        from osgeo import gdal
    except ImportError:
        return
    if gdal.GetConfigOption("CPL_VSIL_CURL_CACHE_SIZE"):
        return

    total = machine.total_memory_mb() or machine.REFERENCE_RAM_MB
    mb = min(BLOCK_CACHE_MAX_MB, max(BLOCK_CACHE_MIN_MB, total // BLOCK_CACHE_SHARE))
    gdal.SetConfigOption("CPL_VSIL_CURL_CACHE_SIZE", str(mb * 1024 * 1024))


def apply_persistent() -> bool:

    if _state["applied"]:
        return True
    try:
        from osgeo import gdal
    except ImportError:
        return False
    size_block_cache()
    per_path = getattr(gdal, "SetPathSpecificOption", None)
    for key, value in PERSISTENT_OPTIONS.items():
        if key in REMOTE_ONLY_OPTIONS and per_path is not None:
            for prefix in STREAMED_PREFIXES:
                per_path(prefix, key, value)




            if gdal.GetConfigOption(key) == value:
                gdal.SetConfigOption(key, None)
        else:
            gdal.SetConfigOption(key, value)

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
def thread_options(gdal, **options):





    set_local = getattr(gdal, "SetThreadLocalConfigOption", None)
    options = _without_broken_threads(gdal, options)
    if set_local is None or not options:
        yield
        return
    get_local = getattr(gdal, "GetThreadLocalConfigOption", lambda *_: None)
    previous = {key: get_local(key, None) for key in options}
    for key, value in options.items():
        set_local(key, value)
    try:
        yield
    finally:
        for key, value in previous.items():
            set_local(key, value)


@contextlib.contextmanager
def scoped_read(gdal=None, **overrides: str):







    if gdal is None:
        try:
            from osgeo import gdal  # type: ignore[no-redef]
        except ImportError:
            yield
            return
    options = _without_broken_threads(gdal, {**SCOPED_OPTIONS, **overrides})
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
_REMOTE_VSI = ("/vsicurl", "/vsis3", "/vsigs", "/vsiaz", "/vsioss", "/vsiswift", "/vsiadls", "/vsihdfs", "/vsiwebhdfs")
_XML_SOURCE_RE = re.compile(
    rb"(?is)<(SourceFilename|SrcDataSource|SourceDataset|IndexDataset)\b([^>]*)>(.*?)</\1\s*>")


def looks_like_xml(head: bytes) -> bool:
    return bytes(head or b"").lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")


def xml_dataset_refusal(body: bytes, base_url: str) -> str | None:

    if not looks_like_xml(body):
        return None

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


def _source_refusal(path: str, seen: set, depth: int = 0) -> str | None:







    from . import security

    body, cut = _xml_head(path, seen)
    if cut:
        return f"{path} makes GDAL read more datasets than the agent can check; open the files it names instead."
    if not body:
        return None
    if re.search(rb"(?is)<GDALTileIndexDataset\b", body):
        return f"{path} is a tile index whose tiles the agent cannot check; open the tiles themselves."
    for _tag, attrs, raw in _XML_SOURCE_RE.findall(body):
        written = html.unescape(raw.decode("utf-8", "replace")).strip()
        text = _dataset_file(written)
        if text == "":
            continue
        if text is None:
            return f"{path} makes GDAL read {written[:200]}, a source the agent cannot check; open that file itself."
        if not (text.startswith("/vsi") or os.path.isabs(text) or security._is_remote_share(text)):


            beside = b'relativetovrt="1"' in attrs.lower().replace(b" ", b"").replace(b"'", b'"')
            base = os.path.dirname(path) if beside else os.getcwd()
            text = posixpath.join(base, text) if base.startswith("/vsi") else os.path.normpath(os.path.join(base, text))
        error = security.validate_read(text)
        if error:
            return error
        if not text.startswith("/vsi") and not os.path.exists(text):
            return f"{path} makes GDAL read {written[:200]}, which the agent cannot find; open that file itself."
        error = _source_refusal(text, seen, depth + 1)
        if error:
            return error
    return None


def _xml_head(path: str, seen: set) -> tuple[bytes, bool]:


    from . import security

    key = path if path.lower().startswith("/vsi") else os.path.normcase(os.path.abspath(path))
    if key in seen:
        return b"", False
    if len(seen) >= 16:
        return b"", True
    seen.add(key)
    if path.lower().startswith("/vsi"):
        kind, _inner = security.unwrap_vsi(path)
        if kind != "local":
            return b"", False
        try:
            from osgeo import gdal

            handle = gdal.VSIFOpenL(path, "rb")
            if not handle:
                return b"", False
            try:
                body = bytes(gdal.VSIFReadL(1, 256, handle) or b"")
                if looks_like_xml(body):
                    body += bytes(gdal.VSIFReadL(1, _XML_DATASET_MAX_BYTES + 1, handle) or b"")
            finally:
                gdal.VSIFCloseL(handle)
        except Exception:  # noqa: BLE001
            return b"", False
    else:
        if not os.path.isfile(path):
            return b"", False
        try:
            with open(path, "rb") as handle:
                body = handle.read(256)
                if looks_like_xml(body):
                    body += handle.read(_XML_DATASET_MAX_BYTES + 1)
        except OSError:
            return b"", False
    if not looks_like_xml(body):
        return b"", False
    return body, len(body) > 256 + _XML_DATASET_MAX_BYTES


_DRIVER_RE = re.compile(r"(?i)^([a-z][a-z0-9_]{1,15}):(.+)$")

_CONNECTIONS = frozenset({"PG", "POSTGRESQL", "MYSQL", "MSSQL", "OCI", "ODBC", "PGEO", "MONGODBV3", "MONGODB",
                          "ELASTICSEARCH", "ES", "COUCHDB", "CARTO", "SDE", "INGRES", "HANA", "DB2ODBC", "WFS",
                          "OAPIF", "WMS", "WCS", "EEDA", "EEDAI", "PLSCENES", "NGW", "AMIGOCLOUD"})


def _dataset_file(text: str):




    low = text.lower()
    if not text or low.startswith(_REMOTE_VSI) or re.match(r"(?i)(https?|ftp)://", text):
        return ""
    if low.startswith("vrt://"):
        return text[6:].split("?", 1)[0] or None
    if re.match(r"(?i)file://", text):
        return None
    match = _DRIVER_RE.match(text)
    if not match or re.match(r"(?i)^[a-z]:[\\/]", text):
        return text
    name, rest = match.group(1).upper(), match.group(2).strip()
    if name in _CONNECTIONS:
        return ""
    if name == "GTI" or rest.startswith("//"):
        return None
    if rest[:1] in "\"'":
        return rest[1:].split(rest[0], 1)[0] or None
    rest = re.sub(r"^\d+:", "", rest)
    head = re.sub(r":[^:/\\]+$", "", rest)
    return head if len(head) > 2 or rest[1:2] != ":" else rest


def dataset_scope_refusal(path: str) -> str | None:


    from . import security

    low = path.lower()
    if low.endswith((".gti.gpkg", ".gti.fgb", ".gti.parquet")):
        return f"{path} is a tile index whose tiles the agent cannot check; open the tiles themselves."

    start = path if low.startswith("/vsi") else security.expand_path(path)
    if security.refused_share(start):
        return None
    return _source_refusal(start, set())


def opened_files_refusal(dataset) -> str | None:


    from . import security

    try:
        files = list(dataset.GetFileList() or [])
    except Exception:  # noqa: BLE001
        return None
    for name in files:
        if not name or name.lower().startswith("/vsimem/"):
            continue
        error = security.validate_read(name)
        if error:
            return error
    return None


def remote_dataset_refusal(url: str) -> str | None:







    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if security_urls._routed_public_host(parts.hostname or ""):
        return None
    try:
        response = net.open_url(url, timeout=15)
    except net.FetchCancelled:
        raise
    except net.LocalUrlRefused as exc:

        return str(getattr(exc, "reason", "") or exc) or f"{url} leads to an address this plugin does not fetch"
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
        except net.FetchCancelled:
            raise
        except Exception:  # noqa: BLE001
            return None
        return xml_dataset_refusal(head + rest, url)
    finally:
        with contextlib.suppress(Exception):
            response.close()
