# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

from typing import Any

_MISSING = object()


def qt_deleted(obj: Any) -> bool:


    if obj is None:
        return True
    try:
        from qgis.PyQt import sip

        return bool(sip.isdeleted(obj))
    except (ImportError, TypeError):
        return False


def disconnect_quietly(owner: Any, signal: str, *slot) -> None:





    try:
        getattr(owner, signal).disconnect(*slot)
    except Exception:  # nosec B110  # noqa: BLE001
        pass


def message_log_signal_name() -> str:





    from qgis.core import QgsApplication

    new = "messageReceivedWithFormat"
    return new if hasattr(QgsApplication.messageLog(), new) else "messageReceived"


def message_log_signal() -> Any:
    from qgis.core import QgsApplication

    return getattr(QgsApplication.messageLog(), message_log_signal_name())


def one_shot(parent: Any, slot, interval_ms: int | None = None):




    from qgis.PyQt.QtCore import QTimer

    timer = QTimer(parent)
    timer.setSingleShot(True)
    if interval_ms is not None:
        timer.setInterval(interval_ms)
    timer.timeout.connect(slot)
    return timer


def task_flags(task_cls: Any, hidden: bool) -> Any:






    holder = getattr(task_cls, "Flag", task_cls)
    flags = getattr(holder, "CanCancel", None)
    if flags is None:
        return None



    quiet = getattr(holder, "CancelWithoutPrompt", None)
    if quiet is not None:
        flags = flags | quiet
    if hidden:
        for name in ("Hidden", "Silent"):
            extra = getattr(holder, name, None)
            if extra is not None:
                flags = flags | extra
    return flags


def qgis_iface():





    try:
        import qgis.utils
    except Exception:  # noqa: BLE001
        return None
    return getattr(qgis.utils, "iface", None)


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
_FIELD_TAKES_META_TYPE = 33800
_field_type_cache: dict[str, Any] = {}


def _variant_type(name: str) -> Any:
    try:
        from qgis.PyQt.QtCore import QVariant
    except ImportError:
        return None
    return getattr(QVariant, name, None)


def _meta_type(name: str) -> Any:
    try:
        from qgis.PyQt.QtCore import QMetaType
    except ImportError:
        return None
    wanted = _FIELD_TYPE_NAMES.get(name, name)
    found = getattr(getattr(QMetaType, "Type", QMetaType), wanted, None)
    return found if found is not None else getattr(QMetaType, wanted, None)


def field_type(name: str) -> Any:







    if name in _field_type_cache:
        return _field_type_cache[name]
    try:
        from qgis.core import Qgis

        modern = Qgis.QGIS_VERSION_INT >= _FIELD_TAKES_META_TYPE
    except ImportError:
        modern = False
    order = (_meta_type, _variant_type) if modern else (_variant_type, _meta_type)
    resolved = next((value for value in (find(name) for find in order) if value is not None), None)
    if resolved is None:
        raise AttributeError(f"no field type named {name} on this Qt generation")
    _field_type_cache[name] = resolved
    return resolved
