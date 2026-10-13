@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
where py >/dev/null 2>/dev/null && (set "PY=py -3") || (set "PY=python")
%PY% yeogi_photos.py %*
echo.
pause
