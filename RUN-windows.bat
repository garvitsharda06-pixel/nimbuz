@echo off
rem Dalal Desk: double-click me. A window opens, sets everything up on the first run
rem (a few minutes), then your browser opens the terminal at http://127.0.0.1:8000.
rem Stop it with Ctrl+C or by closing this window.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -Open %*
echo.
echo Dalal Desk stopped.
pause
