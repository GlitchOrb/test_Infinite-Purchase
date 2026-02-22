@echo off
setlocal

REM Build Windows exe for Alpha Predator desktop app
py -m pip install --upgrade pip
py -m pip install pyinstaller pyqt5 pandas numpy

py build_exe.py
if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

echo.
echo Done. EXE path: dist\AlphaPredator\AlphaPredator.exe
endlocal
