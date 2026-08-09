@echo off
title FalloutWorld setup
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0FoM_Setup.ps1" %*
if errorlevel 1 pause
