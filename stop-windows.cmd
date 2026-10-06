@echo off
rem Trading-Agent-CN: stop. Normally just close the window opened by start-windows.cmd;
rem use this when that window is gone or a port is reported busy.
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\stop.ps1" %*
echo.
pause
