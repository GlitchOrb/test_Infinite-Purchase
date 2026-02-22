from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from updater import (
    close_running_process_windows,
    download_file,
    parse_sha256_file,
    safe_apply_update,
)
from version import CURRENT_VERSION

GITHUB_OWNER = os.environ.get("INFINITE_PURCHASE_GH_OWNER", "GlitchOrb")
GITHUB_REPO = os.environ.get("INFINITE_PURCHASE_GH_REPO", "test_Infinite-Purchase")
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
APP_EXE_NAME = "InfinitePurchaseApp.exe"
LAUNCHER_EXE_NAME = "InfinitePurchaseLauncher.exe"
INSTALLER_EXE_NAME = "InfinitePurchaseInstaller.exe"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _parse_version(v: str) -> tuple[int, ...]:
    cleaned = v.strip().lstrip("vV")
    out: list[int] = []
    for part in cleaned.split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _http_json(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "InfinitePurchaseLauncher"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Invalid JSON response")
    return data


def _asset_url_map(release_data: Dict[str, Any]) -> Dict[str, str]:
    assets = release_data.get("assets")
    out: Dict[str, str] = {}
    if not isinstance(assets, list):
        return out
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", "")).strip()
        url = str(asset.get("browser_download_url", "")).strip()
        if name and url:
            out[name] = url
    return out


def _find_checksum_for(asset_name: str, assets: Dict[str, str]) -> Optional[str]:
    candidates = [
        f"{asset_name}.sha256",
        "checksums.txt",
        "SHA256SUMS",
        "sha256sums.txt",
    ]
    target_url = None
    for c in candidates:
        if c in assets:
            target_url = assets[c]
            break
    if not target_url:
        return None

    with tempfile.TemporaryDirectory(prefix="infinite_purchase_checksum_") as td:
        path = Path(td) / "checksums.txt"
        download_file(target_url, path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        return parse_sha256_file(text, asset_name)


def _launch_app(base: Path) -> None:
    if getattr(sys, "frozen", False):
        app_exe = base / APP_EXE_NAME
        if app_exe.exists():
            subprocess.Popen([str(app_exe)], cwd=str(base))
            return

    app_py = base / "app.py"
    subprocess.Popen([sys.executable, str(app_py)], cwd=str(base))


def _run_installer_asset(installer_path: Path) -> None:
    if os.name != "nt":
        return
    subprocess.Popen([str(installer_path)], cwd=str(installer_path.parent))


def main() -> int:
    base = _base_dir()
    app_target = base / APP_EXE_NAME

    try:
        release_data = _http_json(LATEST_RELEASE_API)
        latest_tag = str(release_data.get("tag_name", "")).strip()
        if latest_tag and _is_newer(latest_tag, CURRENT_VERSION):
            assets = _asset_url_map(release_data)
            app_asset_url = assets.get(APP_EXE_NAME)
            if app_asset_url:
                expected_sha = _find_checksum_for(APP_EXE_NAME, assets)
                safe_apply_update(
                    asset_url=app_asset_url,
                    target_binary=app_target,
                    expected_sha256=expected_sha,
                    close_running=close_running_process_windows,
                )
            else:
                installer_url = assets.get(INSTALLER_EXE_NAME)
                if installer_url:
                    with tempfile.TemporaryDirectory(prefix="infinite_purchase_installer_") as td:
                        installer_path = Path(td) / INSTALLER_EXE_NAME
                        download_file(installer_url, installer_path)
                        sha = _find_checksum_for(INSTALLER_EXE_NAME, assets)
                        if sha:
                            from updater import verify_sha256, verify_signature_placeholder

                            verify_sha256(installer_path, sha)
                            verify_signature_placeholder(installer_path)
                            _run_installer_asset(installer_path)
    except Exception:
        # Never block app launch due to updater failures.
        pass

    _launch_app(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
