# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

from typing import Any

_MISSING = object()


def enum_member(owner: Any, scope: str, member: str, default: Any = _MISSING) -> Any:






    holder = getattr(owner, scope, None)
    if holder is not None:
        found = getattr(holder, member, _MISSING)
        if found is not _MISSING:
            return found
    found = getattr(owner, member, _MISSING)
    if found is not _MISSING:
        return found
    if default is not _MISSING:
        return default
    name = getattr(owner, "__name__", str(owner))
    raise AttributeError(f"{name} has neither {scope}.{member} nor {member}")















_FIELD_TYPE_NAMES = {
    "String": "QString",
    "Int": "Int",
    "LongLong": "LongLong",
    "Double": "Double",
    "Bool": "Bool",
    "Date": "QDate",
    "DateTime": "QDateTime",
    "Time": "QTime",
    "ByteArray": "QByteArray",
}
_field_type_cache: dict[str, Any] = {}


def field_type(name: str) -> Any:






    if name in _field_type_cache:
        return _field_type_cache[name]
    resolved = None
    try:
        from qgis.PyQt.QtCore import QVariant

        resolved = getattr(QVariant, name, None)
    except ImportError:
        resolved = None
    if resolved is None:
        try:
            from qgis.PyQt.QtCore import QMetaType

            holder = getattr(QMetaType, "Type", QMetaType)
            resolved = getattr(holder, _FIELD_TYPE_NAMES.get(name, name), None)
        except ImportError:
            resolved = None
    if resolved is None:
        raise AttributeError(f"no field type named {name} on this Qt generation")
    _field_type_cache[name] = resolved
    return resolved
