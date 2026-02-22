@echo off
setlocal

REM Build main app executable (single-file, no console)
pyinstaller --noconfirm --clean --onefile --noconsole --name InfinitePurchaseApp --collect-all PyQt5 app.py

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo Build success: dist\InfinitePurchaseApp.exe
exit /b 0
