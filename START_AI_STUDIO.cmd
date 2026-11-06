@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
  py -3 app\launch_workbench.py %*
) else (
  python app\launch_workbench.py %*
)
set "AAS_EXIT=%ERRORLEVEL%"
if not "%AAS_EXIT%"=="0" (
  echo.
  echo Launcher exited with code %AAS_EXIT%. Read the message above.
  pause
)
exit /b %AAS_EXIT%
