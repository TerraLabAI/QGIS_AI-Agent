# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

import hashlib

from .logger import log, log_warning

_ROOTS = {



    "efc65cadbb59adb6efe84da22311b35624b71b3b1ea0da8b6655174ec8978646": (  # pragma: allowlist secret
        "Telekom Security TLS RSA Root 2023", b"""\
-----BEGIN CERTIFICATE-----
MIIFszCCA5ugAwIBAgIQIZxULej27HF3+k7ow3BXlzANBgkqhkiG9w0BAQwFADBj
MQswCQYDVQQGEwJERTEnMCUGA1UECgweRGV1dHNjaGUgVGVsZWtvbSBTZWN1cml0
eSBHbWJIMSswKQYDVQQDDCJUZWxla29tIFNlY3VyaXR5IFRMUyBSU0EgUm9vdCAy
MDIzMB4XDTIzMDMyODEyMTY0NVoXDTQ4MDMyNzIzNTk1OVowYzELMAkGA1UEBhMC
REUxJzAlBgNVBAoMHkRldXRzY2hlIFRlbGVrb20gU2VjdXJpdHkgR21iSDErMCkG
A1UEAwwiVGVsZWtvbSBTZWN1cml0eSBUTFMgUlNBIFJvb3QgMjAyMzCCAiIwDQYJ
KoZIhvcNAQEBBQADggIPADCCAgoCggIBAO01oYGA88tKaVvC+1GDrib94W7zgRJ9
cUD/h3VCKSHtgVIs3xLBGYSJwb3FKNXVS2xE1kzbB5ZKVXrKNoIENqil/Cf2SfHV
cp6R+SPWcHu79ZvB7JPPGeplfohwoHP89v+1VmLhc2o0mD6CuKyVU/QBoCcHcqMA
U6DksquDOFczJZSfvkgdmOGjup5czQRxUX11eKvzWarE4GC+j4NSuHUaQTXtvPM6
Y+mpFEXX5lLRbtLevOP1Czvm4MS9Q2QTps70mDdsipWol8hHD/BeEIvnHRz+sTug
BTNoBUGCwQMrAcjnj02r6LX2zWtEtefdi+zqJbQAIldNsLGyMcEWzv/9FIS3R/qy
8XDe24tsNlikfLMR0cN3f1+2JeANxdKz+bi4d9s3cXFH42AYTyS2dTd4uaNir73J
co4vzLuu2+QVUhkHM/tqty1LkCiCc/4YizWN26cEar7qwU02OxY2kTLvtkCJkUPg
8qKrBC7m8kwOFjQgrIfBLX7JZkcXFBGk8/ehJImr2BrIoVyxo/eMbcgByU/J7MT8
rFEz0ciD0cmfHdRHNCk+y7AO+oMLKFjlKdw/fKifybYKu6boRhYPluV75Gp6SG12
mAWl3G0eQh5C2hrgUve1g8Aae3g1LDj1H/1Joy7SWWO/gLCMk3PLNaaZlSJhZQNg
+y+TS/qanIA7AgMBAAGjYzBhMA4GA1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUtqeX
gj10hZv3PJ+TmpV5dVKMbUcwDwYDVR0TAQH/BAUwAwEB/zAfBgNVHSMEGDAWgBS2
p5eCPXSFm/c8n5OalXl1UoxtRzANBgkqhkiG9w0BAQwFAAOCAgEAqMxhpr51nhVQ
pGv7qHBFfLp+sVr8WyP6Cnf4mHGCDG3gXkaqk/QeoMPhk9tLrbKmXauw1GLLXrtm
9S3ul0A8Yute1hTWjOKWi0FpkzXmuZlrYrShF2Y0pmtjxrlO8iLpWA1WQdH6DErw
M807u20hOq6OcrXDSvvpfeWxm4bu4uB9tPcy/SKE8YXJN3nptT+/XOR0so8RYgDd
GGah2XsjX/GO1WfoVNpbOms2b/mBsTNHM3dA+VKq3dSDz4V4mZqTuXNnQkYRIer+
CqkbGmVps4+uFrb2S1ayLfmlyOw7YqPta9BO1UAJpB+Y1zqlklkg5LB9zVtzaL1t
xKITDmcZuI1CfmwMmm6gJC3VRRvcxAIU/oVbZZfKTpBQCHpCNfnqwmbU+AGuHrS+
w6jv/naaoqYfRvaE7fzbzsQCzndILIyy7MMAo+wsVRjBfhnu4S/yrYObnqsZ38aK
L4x35bcF7DvB7L6Gs4a8wPfc5+pbrrLMtTWGS9DiP7bY+A4A7l3j941Y/8+LN+lj
X273CXE2whJdV/LItM3z7gLfEdxquVeEHVlNjM7IDiPCtyaaEBRx/pOyiriA8A4Q
ntOoUAw3gi/q4Iqd4Sw5/7W0cwDk90imc6y/st53BIe0o82bNSQ3+pCTE4FCxpgm
dTdmQRCsu/WU48IxK63nI1bMNSWSs1A=
-----END CERTIFICATE-----
"""),




    "d95d0e8eda79525bf9beb11b14d2100d3294985f0c62d9fabd9cd999eccb7b1d": (  # pragma: allowlist secret
        "HARICA TLS RSA Root CA 2021", b"""\
-----BEGIN CERTIFICATE-----
MIIFpDCCA4ygAwIBAgIQOcqTHO9D88aOk8f0ZIk4fjANBgkqhkiG9w0BAQsFADBs
MQswCQYDVQQGEwJHUjE3MDUGA1UECgwuSGVsbGVuaWMgQWNhZGVtaWMgYW5kIFJl
c2VhcmNoIEluc3RpdHV0aW9ucyBDQTEkMCIGA1UEAwwbSEFSSUNBIFRMUyBSU0Eg
Um9vdCBDQSAyMDIxMB4XDTIxMDIxOTEwNTUzOFoXDTQ1MDIxMzEwNTUzN1owbDEL
MAkGA1UEBhMCR1IxNzA1BgNVBAoMLkhlbGxlbmljIEFjYWRlbWljIGFuZCBSZXNl
YXJjaCBJbnN0aXR1dGlvbnMgQ0ExJDAiBgNVBAMMG0hBUklDQSBUTFMgUlNBIFJv
b3QgQ0EgMjAyMTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAIvC569l
mwVnlskNJLnQDmT8zuIkGCyEf3dRywQRNrhe7Wlxp57kJQmXZ8FHws+RFjZiPTgE
4VGC/6zStGndLuwRo0Xua2s7TL+MjaQenRG56Tj5eg4MmOIjHdFOY9TnuEFE+2uv
a9of08WRiFukiZLRgeaMOVig1mlDqa2YUlhu2wr7a89o+uOkXjpFc5gH6l8Cct4M
pbOfrqkdtx2z/IpZ525yZa31MJQjB/OCFks1mJxTuy/K5FrZx40d/JiZ+yykgmvw
Kh+OC19xXFyuQnspiYHLA6OZyoieC0AJQTPb5lh6/a6ZcMBaD9YThnEvdmn8kN3b
LW7R8pv1GmuebxWMevBLKKAiOIAkbDakO/IwkfN4E8/BPzWr8R0RI7VDIp4BkrcY
AuUR0YLbFQDMYTfBKnya4dC6s1BG7oKsnTH4+yPiAwBIcKMJJnkVU2DzOFytOOqB
AGMUuTNe3QvboEUHGjMJ+E20pwKmafTCWQWIZYVWrkvL4N48fS0ayOn7H6NhStYq
E613TBoYm5EPWNgGVMWX+Ko/IIqmhaZ39qb8HOLubpQzKoNQhArlT4b4UEV4AIHr
W2jjJo3Me1xR9BQsQL4aYB16cmEdH2MtiKrOokWQCPxrvrNQKlr9qEgYRtaQQJKQ
CoReaDH46+0N0x3GfZkYVVYnZS6NRcUk7M7jAgMBAAGjQjBAMA8GA1UdEwEB/wQF
MAMBAf8wHQYDVR0OBBYEFApII6ZgpJIKM+qTW8VX6iVNvRLuMA4GA1UdDwEB/wQE
AwIBhjANBgkqhkiG9w0BAQsFAAOCAgEAPpBIqm5iFSVmewzVjIuJndftTgfvnNAU
X15QvWiWkKQUEapobQk1OUAJ2vQJLDSle1mESSmXdMgHHkdt8s4cUCbjnj1AUz/3
f5Z2EMVGpdAgS1D0NTsY9FVqQRtHBmg8uwkIYtlfVUKqrFOFrJVWNlar5AWMxaja
H6NpvVMPxP/cyuN+8kyIhkdGGvMA9YCRotxDQpSbIPDRzbLrLFPCU3hKTwSUQZqP
JzLB5UkZv/HywouoCjkxKLR9YjYsTewfM7Z+d21+UPCfDtcRj88YxeMn/ibvBZ3P
zzfF0HvaO7AWhAw6k9a+F9sPPg4ZeAnHqQJyIkv3N3a6dcSFA1pj1bF1BcK5vZSt
jBWZp5N99sXzqnTPBIWUmAD04vnKJGW/4GKvyMX6ssmeVkjaef2WdhW+o45WxLM0
/L5H9MG0qPzVMIho7suuyWPEdr6sOBjhXlzPrjoiUevRi7PzKzMHVIf6tLITe7pT
BGIBnfHAT+7hOtSLIBD6Alfm78ELt5BGnBkpjNxvoEppaZS3JGWg/6w/zgH7IS79
aPib8qXPMThcFarmlwDB31qlpzmq6YR/PFGoOtmUW4y/Twhx5duoXNTSpv4Ao8YW
xw/ogM4cKGR0GQjTQuPOAF1/sdwTsOEFy9EgqoZ0njnnkf3/W9b3raYvAwtt41dU
63ZTGI0RmLo=
-----END CERTIFICATE-----
"""),
}

_checked = False


def _sha256(cert) -> str:
    return hashlib.sha256(bytes(cert.toDer())).hexdigest()


def ensure() -> int:

    global _checked
    if _checked:
        return 0
    _checked = True
    try:
        from qgis.core import QgsApplication
        from qgis.PyQt.QtCore import QByteArray
        from qgis.PyQt.QtNetwork import QSslCertificate

        manager = QgsApplication.authManager()
        if manager is None or manager.isDisabled():
            return 0
        known = {_sha256(cert) for cert in manager.trustedCaCertsCache()}
        known |= {_sha256(cert) for cert in manager.databaseCAs()}
        added = []
        for digest, (name, pem) in _ROOTS.items():
            if digest in known:
                continue
            cert = QSslCertificate(QByteArray(pem))
            if cert.isNull() or _sha256(cert) != digest:
                log_warning(f"Bundled root {digest[:12]} does not match its pin; not added.")
                continue
            if manager.storeCertAuthority(cert):
                added.append(name)
        if added:
            manager.rebuildCaCertsCache()
            manager.rebuildTrustedCaCertsCache()
            log(f"QGIS now trusts {', '.join(added)}, a public root the system trusts and QGIS's list missed.")
        return len(added)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Public roots not checked: {exc}")
        return 0
