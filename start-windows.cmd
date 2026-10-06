@echo off
rem Trading-Agent-CN: start. Opens a new window that runs the app (installs first if needed).
rem Close that window (or press Ctrl+C in it) to stop. Details: docs\SETUP-Windows.md
cd /d "%~dp0"
start "Trading-Agent-CN" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start.ps1" %*
