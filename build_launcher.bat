@echo off
setlocal

REM Build launcher executable (single-file, no console)
pyinstaller --noconfirm --clean --onefile --noconsole --name InfinitePurchaseLauncher launcher.py

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo Build success: dist\InfinitePurchaseLauncher.exe
exit /b 0
