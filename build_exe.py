"""Build Windows executable for Alpha Predator desktop app via PyInstaller.

Usage:
  python build_exe.py
"""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "AlphaPredator",
        "--icon",
        "icon.ico",
        "--add-data",
        "icon.png;.",
        "--add-data",
        "icon.ico;.",
        "--hidden-import",
        "PyQt5.sip",
        "--hidden-import",
        "zoneinfo",
        "app.py",
    ]

    print("[build]", " ".join(cmd))
    proc = subprocess.run(cmd, env=os.environ.copy())
    if proc.returncode == 0:
        print("\n✅ Build complete: dist/AlphaPredator/AlphaPredator.exe")
    else:
        print("\n❌ Build failed")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
