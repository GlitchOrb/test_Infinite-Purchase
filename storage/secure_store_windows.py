from __future__ import annotations

import base64
import json
import os
import platform
from typing import Optional, Tuple


class SecureStoreWindows:
    def __init__(self, service_name: str) -> None:
        self.service_name = service_name

    @staticmethod
    def supported() -> bool:
        return platform.system().lower().startswith("win")

    def save_json(self, payload: dict) -> None:
        if not self.supported():
            raise RuntimeError("Secure Windows storage not supported on this OS")
        raw = json.dumps(payload).encode("utf-8")
        try:
            import win32cred  # type: ignore

            win32cred.CredWrite(
                {
                    "Type": win32cred.CRED_TYPE_GENERIC,
                    "TargetName": self.service_name,
                    "UserName": self.service_name,
                    "CredentialBlob": raw,
                    "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
                },
                0,
            )
            return
        except Exception:
            pass

        encrypted = self._dpapi_encrypt(raw)
        with open(self._fallback_path(), "w", encoding="utf-8") as f:
            f.write(base64.b64encode(encrypted).decode("ascii"))

    def load_json(self) -> Optional[dict]:
        if not self.supported():
            return None
        try:
            import win32cred  # type: ignore

            cred = win32cred.CredRead(self.service_name, win32cred.CRED_TYPE_GENERIC)
            return json.loads(bytes(cred["CredentialBlob"]).decode("utf-8"))
        except Exception:
            pass

        try:
            with open(self._fallback_path(), "r", encoding="utf-8") as f:
                enc = base64.b64decode(f.read().strip())
            raw = self._dpapi_decrypt(enc)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def delete(self) -> None:
        if not self.supported():
            return
        try:
            import win32cred  # type: ignore

            win32cred.CredDelete(self.service_name, win32cred.CRED_TYPE_GENERIC, 0)
        except Exception:
            pass
        p = self._fallback_path()
        if os.path.exists(p):
            os.remove(p)

    def _fallback_path(self) -> str:
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, f"{self.service_name.replace('.', '_')}.bin")

    @staticmethod
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

    @staticmethod
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
