from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional


class UpdateError(RuntimeError):
    """Raised when an update step fails safely."""


def download_file(url: str, dest: Path, chunk_size: int = 1024 * 128) -> Path:
    if not url.lower().startswith("https://"):
        raise UpdateError("Only HTTPS URLs are allowed for updates")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, tmp.open("wb") as fp:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                fp.write(chunk)
        tmp.replace(dest)
        return dest
    except Exception as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise UpdateError(f"Download failed: {exc}") from exc


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def parse_sha256_file(checksum_text: str, expected_filename: str) -> Optional[str]:
    expected_filename = expected_filename.strip().lower()
    for raw in checksum_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) == 1 and len(parts[0]) == 64:
            return parts[0].lower()
        if len(parts) >= 2:
            digest = parts[0].lower()
            fname = parts[-1].lstrip("*\\").replace("\\", "/").split("/")[-1].lower()
            if len(digest) == 64 and fname == expected_filename:
                return digest
    return None


def verify_sha256(path: Path, expected_hex: str) -> None:
    actual = compute_sha256(path)
    if actual != expected_hex.lower():
        raise UpdateError("SHA256 verification failed")


def verify_signature_placeholder(path: Path) -> None:
    """Optional hook: integrate Authenticode or certificate pinning here."""
    _ = path


def atomic_replace_with_rollback(src_new_file: Path, dst_file: Path) -> None:
    if not src_new_file.exists():
        raise UpdateError("New file does not exist")

    dst_file.parent.mkdir(parents=True, exist_ok=True)
    backup = dst_file.with_suffix(dst_file.suffix + ".bak")

    try:
        if backup.exists():
            backup.unlink()

        if dst_file.exists():
            os.replace(str(dst_file), str(backup))

        os.replace(str(src_new_file), str(dst_file))

        if backup.exists():
            backup.unlink()
    except Exception as exc:
        try:
            if dst_file.exists() and backup.exists():
                dst_file.unlink(missing_ok=True)
                os.replace(str(backup), str(dst_file))
            elif backup.exists() and not dst_file.exists():
                os.replace(str(backup), str(dst_file))
        except Exception:
            pass
        raise UpdateError(f"Atomic replacement failed: {exc}") from exc


def replace_binary_safely(downloaded_binary: Path, target_binary: Path) -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="infinite_purchase_update_"))
    staged = work_dir / target_binary.name
    try:
        shutil.copy2(downloaded_binary, staged)
        verify_signature_placeholder(staged)
        atomic_replace_with_rollback(staged, target_binary)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def close_running_process_windows(exe_name: str) -> None:
    # Best-effort only; never fatal.
    if os.name != "nt":
        return
    try:
        import subprocess

        subprocess.run(
            ["taskkill", "/IM", exe_name, "/F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return


def safe_apply_update(
    *,
    asset_url: str,
    target_binary: Path,
    expected_sha256: Optional[str],
    close_running: Optional[Callable[[str], None]] = None,
) -> bool:
    """Returns True when update is successfully applied; False when skipped/failed."""

    try:
        with tempfile.TemporaryDirectory(prefix="infinite_purchase_dl_") as td:
            tmp_path = Path(td) / target_binary.name
            download_file(asset_url, tmp_path)

            # Never execute unverified binaries.
            if not expected_sha256:
                return False
            verify_sha256(tmp_path, expected_sha256)

            if close_running:
                close_running(target_binary.name)

            replace_binary_safely(tmp_path, target_binary)
            return True
    except Exception:
        return False
