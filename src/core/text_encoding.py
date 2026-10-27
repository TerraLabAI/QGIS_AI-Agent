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
