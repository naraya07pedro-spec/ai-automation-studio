@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
  py -3 tools\connect_private_vps.py %*
) else (
  python tools\connect_private_vps.py %*
)
set "AAS_EXIT=%ERRORLEVEL%"
if not "%AAS_EXIT%"=="0" (
  echo.
  echo SSH tunnel ended or was blocked. Read the message above.
  pause
)
exit /b %AAS_EXIT%
