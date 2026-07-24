@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo.ps1"
if errorlevel 1 (
  echo.
  echo LocalFit Lab demo could not start. See the message above.
  pause
)
