@echo off
REM FalloutWorld UDP server — localhost only (same-PC clients).
REM For a second PC on your LAN, use start_server_lan.bat instead
REM (or FoM.exe → [3] LAN Host).

title FalloutWorld - Server
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [server] using: %PY%
echo [server] bind:  127.0.0.1:31337  (this PC only)
echo [server] for LAN: run start_server_lan.bat
echo [server] leave this window open. Ctrl+C to stop.
echo.

"%PY%" -m net.server.main --host 127.0.0.1 --port 31337
if errorlevel 1 (
    echo.
    echo [server] exited with error. Press any key.
    pause >nul
)
