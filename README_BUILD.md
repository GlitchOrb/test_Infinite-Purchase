# Infinite Purchase — Windows Build & Distribution

This project uses a **launcher-first deployment model**:

- `InfinitePurchaseLauncher.exe` = updater/version checker + app bootstrap
- `InfinitePurchaseApp.exe` = main PyQt app (`app.py` entry)

Users should run the **Launcher** only.

---

## 1) Build main app EXE

```bat
build_app.bat
```

This runs:

```bat
pyinstaller --onefile --noconsole --name InfinitePurchaseApp --collect-all PyQt5 app.py
```

Output:

- `dist\InfinitePurchaseApp.exe`

---

## 2) Build launcher EXE

```bat
build_launcher.bat
```

This runs:

```bat
pyinstaller --onefile --noconsole --name InfinitePurchaseLauncher launcher.py
```

Output:

- `dist\InfinitePurchaseLauncher.exe`

---

## 3) Build installer (Inno Setup)

1. Open `installer.iss` in Inno Setup Compiler.
2. Build the script.

Output:

- `dist\InfinitePurchaseInstaller.exe`

Installer behavior:

- installs both launcher and app into `{pf}\InfinitePurchase`
- creates Start Menu and Desktop shortcuts to **Launcher**
- can launch Launcher after install

---

## 4) Publish GitHub Release

1. Tag new version (e.g. `v1.0.1`).
2. Create GitHub Release for that tag.
3. Upload at least one of:
   - `InfinitePurchaseApp.exe` (preferred for in-place app update)
   - `InfinitePurchaseInstaller.exe`
4. Upload checksum file:
   - `InfinitePurchaseApp.exe.sha256` or `checksums.txt` / `SHA256SUMS`

Launcher checks:

- `https://api.github.com/repos/<owner>/<repo>/releases/latest`

Owner/repo defaults are:

- owner: `GlitchOrb`
- repo: `test_Infinite-Purchase`

Can be overridden by env vars:

- `INFINITE_PURCHASE_GH_OWNER`
- `INFINITE_PURCHASE_GH_REPO`

---

## 5) Version numbering

`version.py` contains:

```python
CURRENT_VERSION = "1.0.0"
```

- Bump this value when releasing a new version.
- Release tags should be semver-like (`v1.0.1`, `1.0.1`, etc.).
- Launcher compares release tag vs `CURRENT_VERSION`.

---

## 6) Auto-update flow

1. Launcher starts.
2. Fetches latest GitHub release metadata.
3. If newer than local version:
   - downloads update asset via HTTPS
   - verifies SHA256 (if checksum asset exists)
   - performs safe replacement using temp file + atomic rename + rollback
4. Launches `InfinitePurchaseApp.exe`.

Safety behavior:

- update failure never blocks app launch
- no SSL verification bypass
- no execution of unverified binaries
- no overwrite of running binary in-place

---

## 7) Security and signing notes

- Do **not** bundle or ship secrets (Kiwoom keys, Telegram token, local credentials).
- Windows SmartScreen may warn on unsigned binaries.
- For production distribution, use code signing (Authenticode certificate).
- `updater.py` includes `verify_signature_placeholder()` hook for future signature verification integration.
