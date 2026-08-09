@echo off
REM FalloutWorld UDP server — LAN bind (all interfaces).
REM Use this when a second PC on your network will join.
REM Clients: fw_config.ini  →  server = <this_pc_lan_ip>:31337
REM Or use FoM.exe → [3] LAN Host (preferred for friends).

title FalloutWorld - Server (LAN)
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [server] using: %PY%
echo [server] bind:  0.0.0.0:31337  (reachable from other PCs on your LAN)
echo [server] leave this window open. Ctrl+C to stop.
echo.
echo [server] Tell the joiner this machine's LAN IPv4, e.g. 192.168.x.x
echo [server] Windows Firewall: allow inbound UDP 31337 if they cannot connect.
echo.

"%PY%" -m net.server.main --host 0.0.0.0 --port 31337
if errorlevel 1 (
    echo.
    echo [server] exited with error. Press any key.
    pause >nul
)
