@echo off
cd /d "%~dp0"
where py >/dev/null 2>/dev/null && (set "PY=py -3") || (set "PY=python")
echo [1/2] Installing Python packages (playwright, pillow)...
%PY% -m pip install --upgrade playwright pillow
if errorlevel 1 (
  echo.
  echo Install failed. Check that Python is installed and "Add python.exe to PATH" was checked.
  pause
  exit /b 1
)
echo.
echo [2/2] Done. This program uses your installed Google Chrome, so no extra browser download is needed.
echo Now double-click run.bat
pause
