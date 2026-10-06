@echo off
rem Trading-Agent-CN: install / repair. Safe to run again; finished steps are skipped.
rem Usually not needed: start-windows.cmd runs this automatically when something is missing.
rem Details: docs\SETUP-Windows.md
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\setup.ps1" %*
echo.
pause
