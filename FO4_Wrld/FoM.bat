@echo off
REM One-click multiplayer. Prefer FoM.exe from the player pack.
REM Dev fallback: build launcher if needed, then run it.

title FalloutWorld
cd /d "%~dp0"

set "FOM=%~dp0fw_launcher\build\FoM.exe"
if not exist "%FOM%" set "FOM=%~dp0tools\player_setup\dist\FoM_PlayerPack\FoM.exe"

if not exist "%FOM%" (
    echo Building FoM.exe ...
    pushd fw_launcher
    call build.bat
    popd
    set "FOM=%~dp0fw_launcher\build\FoM.exe"
)

if not exist "%FOM%" (
    echo ERROR: FoM.exe missing. Build fw_launcher first.
    pause
    exit /b 1
)

REM Ensure payload dxgi is next to FoM for install step
if exist "%~dp0fw_native\build-minimal\dxgi.dll" (
    if not exist "%~dp0fw_launcher\build\dxgi.dll" (
        copy /Y "%~dp0fw_native\build-minimal\dxgi.dll" "%~dp0fw_launcher\build\dxgi.dll" >nul
    )
)

start "" "%FOM%"
