"""Windows DPAPI protection for provider API keys."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(data: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def protect_secret(secret: str) -> str:
    if not secret:
        return ""
    if os.name != "nt":
        raise RuntimeError("Provider credentials can only be protected on Windows.")
    source, source_buffer = _blob_from_bytes(secret.encode("utf-8"))
    destination = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(destination.pbData)
        del source_buffer
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("dpapi:"):
        return value  # Backward compatibility with profiles created before v1.0.7.
    if os.name != "nt":
        raise RuntimeError("Provider credentials can only be decrypted on Windows.")
    try:
        encrypted = base64.b64decode(value.split(":", 1)[1], validate=True)
    except ValueError as exc:
        raise RuntimeError("Invalid protected provider credential.") from exc
    source, source_buffer = _blob_from_bytes(encrypted)
    destination = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination)
    ):
        raise ctypes.WinError()
    try:
        decrypted = ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(destination.pbData)
        del source_buffer
    return decrypted.decode("utf-8")
