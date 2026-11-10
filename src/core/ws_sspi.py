# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import ctypes
import sys

SCHEMES = ("Negotiate", "NTLM")

_SECPKG_CRED_OUTBOUND = 2
_SECURITY_NATIVE_DREP = 0x10
_SECBUFFER_TOKEN = 2
_SEC_E_OK = 0
_SEC_I_CONTINUE_NEEDED = 0x00090312
_SEC_WINNT_AUTH_IDENTITY_UNICODE = 2

_TOKEN_ROOM = 65536


class _SecHandle(ctypes.Structure):
    _fields_ = [("dwLower", ctypes.c_size_t), ("dwUpper", ctypes.c_size_t)]


class _SecBuffer(ctypes.Structure):
    _fields_ = [("cbBuffer", ctypes.c_ulong), ("BufferType", ctypes.c_ulong), ("pvBuffer", ctypes.c_void_p)]


class _SecBufferDesc(ctypes.Structure):
    _fields_ = [("ulVersion", ctypes.c_ulong), ("cBuffers", ctypes.c_ulong), ("pBuffers", ctypes.POINTER(_SecBuffer))]


class _AuthIdentity(ctypes.Structure):
    _fields_ = [("User", ctypes.c_wchar_p), ("UserLength", ctypes.c_ulong),
                ("Domain", ctypes.c_wchar_p), ("DomainLength", ctypes.c_ulong),
                ("Password", ctypes.c_wchar_p), ("PasswordLength", ctypes.c_ulong),
                ("Flags", ctypes.c_ulong)]


_api: dict = {}


def _secur32():
    lib = _api.get("lib")
    if lib is None:
        lib = ctypes.WinDLL("secur32")
        handle, desc = ctypes.POINTER(_SecHandle), ctypes.POINTER(_SecBufferDesc)
        lib.AcquireCredentialsHandleW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, handle, ctypes.POINTER(ctypes.c_longlong)]
        lib.AcquireCredentialsHandleW.restype = ctypes.c_long
        lib.InitializeSecurityContextW.argtypes = [
            handle, handle, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, desc,
            ctypes.c_ulong, handle, desc, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_longlong)]
        lib.InitializeSecurityContextW.restype = ctypes.c_long
        lib.DeleteSecurityContext.argtypes = [handle]
        lib.FreeCredentialsHandle.argtypes = [handle]
        _api["lib"] = lib
    return lib


def pick(offered) -> str:

    if sys.platform != "win32":
        return ""
    names = {str(name).lower() for name in offered}
    return next((scheme for scheme in SCHEMES if scheme.lower() in names), "")


class Handshake:






    def __init__(self, scheme: str, proxy_host: str, user: str = "",  # nosec B107
                 password: str = ""):
        self._lib = _secur32()
        self._cred = _SecHandle()
        self._ctx = _SecHandle()
        self._has_cred = self._has_ctx = False

        self._target = f"HTTP/{proxy_host}"
        identity = None
        if user:
            domain = ""
            if "\\" in user:
                domain, user = user.split("\\", 1)

            units = [len(text.encode("utf-16-le")) // 2 for text in (user, domain, password)]
            identity = _AuthIdentity(user, units[0], domain, units[1], password, units[2],
                                     _SEC_WINNT_AUTH_IDENTITY_UNICODE)
        expiry = ctypes.c_longlong()
        status = self._lib.AcquireCredentialsHandleW(
            None, scheme, _SECPKG_CRED_OUTBOUND, None, ctypes.byref(identity) if identity is not None else None,
            None, None, ctypes.byref(self._cred), ctypes.byref(expiry))
        if status != _SEC_E_OK:
            raise OSError(f"Windows has no {scheme} credentials (0x{status & 0xFFFFFFFF:08x})")
        self._has_cred = True

    def step(self, challenge: bytes = b"") -> bytes:
        in_desc = None
        if challenge:
            held = ctypes.create_string_buffer(challenge, len(challenge))
            in_buffer = _SecBuffer(len(challenge), _SECBUFFER_TOKEN, ctypes.cast(held, ctypes.c_void_p))
            in_desc = _SecBufferDesc(0, 1, ctypes.pointer(in_buffer))
        room = ctypes.create_string_buffer(_TOKEN_ROOM)
        out_buffer = _SecBuffer(_TOKEN_ROOM, _SECBUFFER_TOKEN, ctypes.cast(room, ctypes.c_void_p))
        out_desc = _SecBufferDesc(0, 1, ctypes.pointer(out_buffer))
        attrs = ctypes.c_ulong()
        expiry = ctypes.c_longlong()
        status = self._lib.InitializeSecurityContextW(
            ctypes.byref(self._cred), ctypes.byref(self._ctx) if self._has_ctx else None, self._target, 0, 0,
            _SECURITY_NATIVE_DREP, ctypes.byref(in_desc) if in_desc is not None else None, 0,
            ctypes.byref(self._ctx), ctypes.byref(out_desc), ctypes.byref(attrs), ctypes.byref(expiry))
        code = status & 0xFFFFFFFF
        if code not in (_SEC_E_OK, _SEC_I_CONTINUE_NEEDED):
            raise OSError(f"Windows refused the proxy sign-in (0x{code:08x})")
        self._has_ctx = True
        return room.raw[:out_buffer.cbBuffer]

    def close(self) -> None:
        if self._has_ctx:
            self._lib.DeleteSecurityContext(ctypes.byref(self._ctx))
            self._has_ctx = False
        if self._has_cred:
            self._lib.FreeCredentialsHandle(ctypes.byref(self._cred))
            self._has_cred = False
