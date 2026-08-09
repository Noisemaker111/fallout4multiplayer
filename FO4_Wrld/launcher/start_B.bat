@echo off
REM FalloutWorld Launcher — Player B (ColdClient + FO4_b steamless)
REM Double-click to start. Leave this window open.
REM NOTE: Server + Player A must be running FIRST.
REM Requires FO4_b directory. See docs\MP_TEST_RUNBOOK.md

title FalloutWorld - Player B

cd /d "%~dp0\.."
if exist "%~dp0..\.venv\Scripts\python.exe" (
    "%~dp0..\.venv\Scripts\python.exe" -m launcher.main --side B --no-server
) else (
    python -m launcher.main --side B --no-server
)

echo.
echo Launcher exited. Press any key to close.
pause >nul
