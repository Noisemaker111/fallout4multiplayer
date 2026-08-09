@echo off
REM FalloutWorld Launcher — Player A (Steam + F4SE)
REM Double-click to start. Leave this window open.
REM Prefer: start_server.bat first, then this (or let A auto-start server).
REM Multiplayer checklist: docs\MP_TEST_RUNBOOK.md

title FalloutWorld - Player A

cd /d "%~dp0\.."
if exist "%~dp0..\.venv\Scripts\python.exe" (
    "%~dp0..\.venv\Scripts\python.exe" -m launcher.main --side A
) else (
    python -m launcher.main --side A
)

echo.
echo Launcher exited. Press any key to close.
pause >nul
