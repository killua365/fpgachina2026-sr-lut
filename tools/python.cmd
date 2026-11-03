@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0initialize_runtime.ps1" %*
exit /b %errorlevel%
