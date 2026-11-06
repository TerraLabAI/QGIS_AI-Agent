# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

from ..core import tool_rules
from ..core.tool_registry import coded_fact




def code_help(exc_type: str, message: str) -> dict | str:


    text = str(message or "")
    served = tool_rules.section("exception_hints")
    for needle, code in served.get("signatures") or ():
        if needle in text:
            return coded_fact(hint=code)
    code = (served.get("types") or {}).get(str(exc_type or ""))
    return coded_fact(hint=code) if code else ""
