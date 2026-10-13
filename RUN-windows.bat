@echo off
rem Dalal Desk: double-click me. A window opens, sets everything up on the first run
rem (a few minutes), then your browser opens the terminal at http://127.0.0.1:8000.
rem Stop it with Ctrl+C or by closing this window.
cd /d "%~dp0"
if exist "%~dp0scripts\start.ps1" goto run
echo This file was started from inside the zip, so the rest of Dalal Desk is missing.
echo Right-click Dalal-Desk.zip, choose "Extract All", then double-click RUN-windows.bat
echo inside the extracted Dalal-Desk folder.
echo.
pause
exit /b 1
:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -Open %*
echo.
echo Dalal Desk stopped.
pause
