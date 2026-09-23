# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

from email.message import Message
from email.utils import collapse_rfc2231_value


def _parsed(name: str, value) -> Message:
    message = Message()
    message[name] = str(value or "")
    return message


def disposition_filename(header) -> str:





    names = [value for key, value in _parsed("content-disposition", header).get_params(
        [], header="content-disposition") if key == "filename"]
    encoded = [value for value in names if isinstance(value, tuple)]
    chosen = (encoded or names or [""])[0]
    return collapse_rfc2231_value(chosen).strip() if chosen else ""


def declared_charset(content_type) -> str:

    found = _parsed("content-type", content_type).get_content_charset() or ""
    return found.strip("'\" ")
