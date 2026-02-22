"""Windows-only secure credential storage for login persistence."""

from __future__ import annotations

import base64
import json
import platform
from typing import Optional, Tuple

SERVICE_NAME = "AlphaPredator.KiwoomREST"


def _is_windows() -> bool:
    return platform.system().lower().startswith("win")


def is_remember_supported() -> bool:
    return _is_windows()


def save_credentials(app_key: str, app_secret: str, account_no: str) -> None:
    if not _is_windows():
        raise RuntimeError("Remember login is supported on Windows only")

    payload = json.dumps(
        {"app_key": app_key, "app_secret": app_secret, "account_no": account_no}
    ).encode("utf-8")

    try:
        import win32cred  # type: ignore

        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": SERVICE_NAME,
                "UserName": account_no,
                "CredentialBlob": payload,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )
        return
    except Exception:
        pass

    encrypted = _dpapi_encrypt(payload)
    import os

    path = _fallback_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(base64.b64encode(encrypted).decode("ascii"))


def load_credentials() -> Optional[Tuple[str, str, str]]:
    if not _is_windows():
        return None

    try:
        import win32cred  # type: ignore

        cred = win32cred.CredRead(SERVICE_NAME, win32cred.CRED_TYPE_GENERIC)
        blob = bytes(cred["CredentialBlob"])
        data = json.loads(blob.decode("utf-8"))
        return data["app_key"], data["app_secret"], data["account_no"]
    except Exception:
        pass

    try:
        path = _fallback_path()
        with open(path, "r", encoding="utf-8") as f:
            enc = base64.b64decode(f.read().strip())
        raw = _dpapi_decrypt(enc)
        data = json.loads(raw.decode("utf-8"))
        return data["app_key"], data["app_secret"], data["account_no"]
    except Exception:
        return None


def delete_credentials() -> None:
    if not _is_windows():
        return

    try:
        import win32cred  # type: ignore

        win32cred.CredDelete(SERVICE_NAME, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        pass

    import os

    path = _fallback_path()
    if os.path.exists(path):
        os.remove(path)


def _fallback_path() -> str:
    import os

    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "alpha_predator_rest_cred.bin")


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
