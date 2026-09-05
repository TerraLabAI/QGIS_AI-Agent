# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One way to name an enum member that works on QGIS 3 and QGIS 4."""
























from __future__ import annotations

from typing import Any

_MISSING = object()


def enum_member(owner: Any, scope: str, member: str, default: Any = _MISSING) -> Any:
    """The scoped member on Qt6, the flat one on Qt5, both looked up by name."""





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
    """The value ``QgsField`` wants for a field of this kind, on either generation."""





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
