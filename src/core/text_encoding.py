# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import codecs

from .host_platform import IS_WINDOWS


def detect_encoding(sample: bytes) -> str:









    if sample.startswith(codecs.BOM_UTF8):
        return "UTF-8"
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):

        return "UTF-16"
    try:



        codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
    except UnicodeDecodeError:
        return ansi_encoding()
    return "UTF-8"




_ANSI_NAMES = {874: "cp874", 932: "Shift_JIS", 936: "GBK", 949: "EUC-KR", 950: "Big5",
               **{page: f"windows-{page}" for page in range(1250, 1259)}}





_PYTHON_CODECS = {"shift_jis": "cp932", "euc_kr": "cp949", "big5": "cp950"}


def python_codec(name: str) -> str:






    for spelling in (name, name.lower().replace("windows-", "cp")):
        try:
            canonical = codecs.lookup(spelling).name
        except LookupError:
            continue
        return _PYTHON_CODECS.get(canonical, canonical)
    return name


def spellings(name: str) -> list[str]:


    out = [name]
    try:
        canonical = codecs.lookup(name).name
    except LookupError:
        return out
    out += [canonical, canonical.replace("_", "-")]
    if canonical.startswith("cp") and canonical[2:].isdigit():
        out.append("windows-" + canonical[2:])
    if canonical.startswith("iso8859-"):
        out.append("ISO-8859-" + canonical[8:])
    return out


def ansi_encoding() -> str:







    if IS_WINDOWS:
        try:
            import ctypes

            name = _ANSI_NAMES.get(int(ctypes.windll.kernel32.GetACP()))
            if name:
                return name
        except Exception:  # nosec B110
            pass
    return "windows-1252"
