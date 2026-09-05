# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The GDAL settings that make a remote file open in a second instead of ten."""







































from __future__ import annotations

import contextlib











VSICURL_ALLOWED_EXTENSIONS = (".parquet", ".geoparquet", ".fgb", ".gpkg", ".pmtiles", ".tif", ".tiff",
                              ".vrt", ".laz", ".las", ".vpc", "{noext}")



STREAMED_PREFIXES = ("/vsicurl", "/vsis3", "/vsigs", "/vsiaz", "/vsioss", "/vsiswift", "/vsihdfs")


def streamed_in_place(layer) -> bool:
    """Whether the layer is read over HTTP where it is published."""







    if layer is None:
        return False
    try:
        source = str(layer.source() or "")
    except Exception:  # noqa: BLE001 - a layer that cannot name its source is not streamed
        return False
    return any(prefix in source for prefix in STREAMED_PREFIXES)




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



SCOPED_OPTIONS: dict[str, str] = {
    **PERSISTENT_OPTIONS,
    "CPL_VSIL_CURL_CHUNK_SIZE": "1048576",
    "GDAL_NUM_THREADS": "ALL_CPUS",






    "CPL_VSIL_CURL_AUTHORIZATION_HEADER_ALLOWED_IF_REDIRECT": "NO",
}

_applied = False


def _qgis_extra_cas() -> bytes:
    """The CAs QGIS trusts that the system does not, as PEM."""








    try:
        from qgis.core import QgsApplication
        from qgis.PyQt.QtNetwork import QSslConfiguration

        manager = QgsApplication.authManager()
        trusted = manager.trustedCaCerts() if manager is not None else []
        system = {bytes(cert.digest()).hex() for cert in QSslConfiguration.systemCaCertificates()}
        extra = [cert for cert in trusted if bytes(cert.digest()).hex() not in system]
        return b"".join(bytes(cert.toPem()) for cert in extra)
    except Exception:  # noqa: BLE001 - no QGIS, or a Qt build without the call
        return b""


def _ca_bundle_with_qgis_extras():
    """Path to a CA file that keeps curl's own roots and adds the company's."""











    extra = _qgis_extra_cas()
    if not extra:
        return None
    import os

    from .host_platform import retry_file_op
    from .policy import AGENT_HOME
    from .security import safe_open

    current = None
    try:
        from osgeo import gdal

        current = gdal.GetConfigOption("GDAL_HTTP_CAINFO")
    except Exception:  # noqa: BLE001 - asked before GDAL is importable  # nosec B110 - the environment answers next
        pass
    current = current or os.environ.get("CURL_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if not current or not os.path.exists(current):


        return None
    with open(current, "rb") as handle:
        base = handle.read()
    if extra in base:
        return None
    target = os.path.join(AGENT_HOME, "certs", "gdal-ca-bundle.pem")
    os.makedirs(os.path.dirname(target), exist_ok=True)


    temp = target + ".new"
    with safe_open(temp, "wb") as handle:
        handle.write(base if base.endswith(b"\n") else base + b"\n")
        handle.write(extra)
    try:
        retry_file_op(os.replace, temp, target)
    except OSError:
        return target if os.path.exists(target) else None
    return target


def apply_persistent() -> bool:
    """Set the process-wide options once. True when GDAL was reachable."""
    global _applied
    if _applied:
        return True
    try:
        from osgeo import gdal
    except ImportError:
        return False
    for key, value in PERSISTENT_OPTIONS.items():
        gdal.SetConfigOption(key, value)
    try:
        bundle = _ca_bundle_with_qgis_extras()
    except Exception:  # noqa: BLE001 - a remote read without it beats no remote read
        bundle = None
    if bundle:
        gdal.SetConfigOption("GDAL_HTTP_CAINFO", bundle)
    _applied = True
    return True


@contextlib.contextmanager
def scoped_read(gdal=None, **overrides: str):
    """Apply the read options for this block only, then put back what was there."""






    if gdal is None:
        try:
            from osgeo import gdal
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
