"""Windows-only secure credential storage for login and Telegram persistence."""

from __future__ import annotations

import base64
import json
import platform
from typing import Optional, Tuple

SERVICE_NAME = "AlphaPredator.KiwoomREST"
TELEGRAM_SERVICE_NAME = "AlphaPredator.Telegram"


def _is_windows() -> bool:
    return platform.system().lower().startswith("win")


def is_remember_supported() -> bool:
    return _is_windows()


def save_credentials(app_key: str, app_secret: str, account_no: str) -> None:
    _save_payload(
        service_name=SERVICE_NAME,
        fallback_filename="alpha_predator_rest_cred.bin",
        username=account_no,
        payload={"app_key": app_key, "app_secret": app_secret, "account_no": account_no},
    )


def load_credentials() -> Optional[Tuple[str, str, str]]:
    data = _load_payload(
        service_name=SERVICE_NAME,
        fallback_filename="alpha_predator_rest_cred.bin",
    )
    if not data:
        return None
    app_key = str(data.get("app_key", "")).strip()
    app_secret = str(data.get("app_secret", "")).strip()
    account_no = str(data.get("account_no", "")).strip()
    if not app_key or not app_secret or not account_no:
        return None
    return app_key, app_secret, account_no


def delete_credentials() -> None:
    _delete_payload(service_name=SERVICE_NAME, fallback_filename="alpha_predator_rest_cred.bin")


def save_telegram_credentials(token: str, chat_id: str) -> None:
    _save_payload(
        service_name=TELEGRAM_SERVICE_NAME,
        fallback_filename="alpha_predator_telegram_cred.bin",
        username="telegram",
        payload={"token": token, "chat_id": chat_id},
    )


def load_telegram_credentials() -> Optional[Tuple[str, str]]:
    data = _load_payload(
        service_name=TELEGRAM_SERVICE_NAME,
        fallback_filename="alpha_predator_telegram_cred.bin",
    )
    if not data:
        return None
    token = str(data.get("token", "")).strip()
    chat_id = str(data.get("chat_id", "")).strip()
    if not token or not chat_id:
        return None
    return token, chat_id


def delete_telegram_credentials() -> None:
    _delete_payload(service_name=TELEGRAM_SERVICE_NAME, fallback_filename="alpha_predator_telegram_cred.bin")


def _save_payload(service_name: str, fallback_filename: str, username: str, payload: dict) -> None:
    if not _is_windows():
        raise RuntimeError("Remember settings are supported on Windows only")

    raw = json.dumps(payload).encode("utf-8")

    try:
        import win32cred  # type: ignore

        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": service_name,
                "UserName": username,
                "CredentialBlob": raw,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )
        return
    except Exception:
        pass

    encrypted = _dpapi_encrypt(raw)
    path = _fallback_path(fallback_filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(base64.b64encode(encrypted).decode("ascii"))


def _load_payload(service_name: str, fallback_filename: str) -> Optional[dict]:
    if not _is_windows():
        return None

    try:
        import win32cred  # type: ignore

        cred = win32cred.CredRead(service_name, win32cred.CRED_TYPE_GENERIC)
        blob = bytes(cred["CredentialBlob"])
        parsed = json.loads(blob.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    try:
        path = _fallback_path(fallback_filename)
        with open(path, "r", encoding="utf-8") as f:
            enc = base64.b64decode(f.read().strip())
        raw = _dpapi_decrypt(enc)
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _delete_payload(service_name: str, fallback_filename: str) -> None:
    if not _is_windows():
        return

    try:
        import win32cred  # type: ignore

        win32cred.CredDelete(service_name, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        pass

    import os

    path = _fallback_path(fallback_filename)
    if os.path.exists(path):
        os.remove(path)


def _fallback_path(filename: str) -> str:
    import os

    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, filename)


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
