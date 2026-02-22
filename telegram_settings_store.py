"""Encrypted storage for Telegram token/chat settings (Windows only)."""

from __future__ import annotations

import base64
import json
import os
import platform
from typing import Optional, Tuple

SERVICE_NAME = "AlphaPredator.Telegram"


def is_supported() -> bool:
    return platform.system().lower().startswith("win")


def save_settings(token: str, chat_id: str, enabled: bool) -> None:
    if not is_supported():
        raise RuntimeError("Remember settings is supported on Windows only")
    payload = json.dumps({"token": token, "chat_id": chat_id, "enabled": bool(enabled)}).encode("utf-8")

    try:
        import win32cred  # type: ignore

        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": SERVICE_NAME,
                "UserName": "telegram",
                "CredentialBlob": payload,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )
        return
    except Exception:
        pass

    enc = _dpapi_encrypt(payload)
    with open(_fallback_path(), "w", encoding="utf-8") as f:
        f.write(base64.b64encode(enc).decode("ascii"))


def load_settings() -> Optional[Tuple[str, str, bool]]:
    if not is_supported():
        return None
    try:
        import win32cred  # type: ignore

        cred = win32cred.CredRead(SERVICE_NAME, win32cred.CRED_TYPE_GENERIC)
        data = json.loads(bytes(cred["CredentialBlob"]).decode("utf-8"))
        return str(data.get("token", "")), str(data.get("chat_id", "")), bool(data.get("enabled", False))
    except Exception:
        pass

    try:
        with open(_fallback_path(), "r", encoding="utf-8") as f:
            enc = base64.b64decode(f.read().strip())
        raw = _dpapi_decrypt(enc)
        data = json.loads(raw.decode("utf-8"))
        return str(data.get("token", "")), str(data.get("chat_id", "")), bool(data.get("enabled", False))
    except Exception:
        return None


def delete_settings() -> None:
    if not is_supported():
        return
    try:
        import win32cred  # type: ignore

        win32cred.CredDelete(SERVICE_NAME, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        pass
    if os.path.exists(_fallback_path()):
        os.remove(_fallback_path())


def _fallback_path() -> str:
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "alpha_predator_telegram.bin")


def _dpapi_encrypt(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise RuntimeError("DPAPI encryption failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_decrypt(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise RuntimeError("DPAPI decryption failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
