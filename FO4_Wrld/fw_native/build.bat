@echo off
REM fw_native build wrapper. Locates MSVC, sources vcvars64, then runs the
REM Ninja-backed CMake preset.
REM
REM Re-run is safe: CMake is incremental, Ninja only rebuilds changed TUs.
REM
REM Toolchain discovery order:
REM   1. %FW_VCVARS%   — explicit override, wins if set
REM   2. vswhere       — any VS 2017+ install carrying the C++ x64 tools
REM   3. E:\BuildTools — legacy hardcoded path (original dev machine)

setlocal EnableExtensions EnableDelayedExpansion

REM --- 0. build flavour ------------------------------------------------------
REM   build.bat             -> full build          -> build\dxgi.dll
REM   build.bat --minimal   -> FW_MINIMAL build    -> build-minimal\dxgi.dll
REM
REM The minimal flavour compiles out every subsystem listed in
REM minimal_exclude.txt (NPC combat sync, containers, doors, equipment, M9 RE
REM diagnostics), so the 1.10.163 port only has to re-derive the addresses that
REM a first playable co-op session actually uses. See docs\START_HERE.md.
REM
REM Separate binaryDir per flavour: switching back and forth does not force a
REM full CMake reconfigure each time.
set "PRESET=msvc-release"
set "OUTDIR=build"
if /i "%~1"=="--minimal" (
    set "PRESET=msvc-minimal"
    set "OUTDIR=build-minimal"
) else if not "%~1"=="" (
    echo [build] ERROR: unknown argument "%~1"  ^(expected --minimal or nothing^)
    exit /b 1
)

set "VCVARS="

REM --- 1. explicit override --------------------------------------------------
if defined FW_VCVARS (
    if exist "%FW_VCVARS%" (
        set "VCVARS=%FW_VCVARS%"
    ) else (
        echo [build] WARNING: FW_VCVARS set but not found: %FW_VCVARS%
    )
)

REM --- 2. vswhere ------------------------------------------------------------
REM vswhere ships with every VS 2017+ installer and always lives at this fixed
REM path, so this finds Build Tools and full VS installs alike with no version
REM guessing. -requires filters out installs lacking the C++ x64 toolset.
REM Capture vswhere's output via a temp file rather than `for /f "usebackq"`:
REM with usebackq, a command starting with a quoted path gets its outer quotes
REM stripped and then splits on the space in "Program Files (x86)".
REM
REM Note: vcvars64.bat itself prints "'vswhere.exe' is not recognized" to
REM stderr on some installs (verified: it happens calling vcvars64 directly,
REM with no wrapper involved). It is harmless — vcvars still sets up the
REM environment and the build succeeds. Not ours to fix; don't chase it.
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if defined VCVARS goto :have_vcvars
if not exist "%VSWHERE%" goto :have_vcvars
"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath > "%TEMP%\fw_vswhere.txt" 2>nul
if not exist "%TEMP%\fw_vswhere.txt" goto :have_vcvars
set /p VSINSTALL=<"%TEMP%\fw_vswhere.txt"
del "%TEMP%\fw_vswhere.txt" >nul 2>&1
if defined VSINSTALL if exist "%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat"
:have_vcvars

REM --- 3. legacy fallback ----------------------------------------------------
if not defined VCVARS (
    if exist "E:\BuildTools\VC\Auxiliary\Build\vcvars64.bat" (
        set "VCVARS=E:\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    )
)

if not defined VCVARS (
    echo [build] ERROR: no MSVC x64 toolchain found.
    echo [build]        Install "Desktop development with C++", or set
    echo [build]        FW_VCVARS to your vcvars64.bat.
    echo [build]        winget install Microsoft.VisualStudio.2022.BuildTools
    exit /b 1
)

echo [build] toolchain: %VCVARS%
call "%VCVARS%" >nul
if errorlevel 1 (
    echo [build] ERROR: vcvars64 failed
    exit /b 1
)

cd /d "%~dp0"

REM MinHook is vendored under deps/ and is not tracked in the repo.
if not exist "deps\MinHook\CMakeLists.txt" (
    echo [build] deps\MinHook missing - cloning...
    git clone --depth 1 https://github.com/TsudaKageyu/minhook.git deps\MinHook
    if errorlevel 1 (
        echo [build] ERROR: failed to clone MinHook
        exit /b 1
    )
)

echo [build] preset: %PRESET%

cmake --preset=%PRESET%
if errorlevel 1 (
    echo [build] ERROR: cmake configure failed
    exit /b 1
)

cmake --build --preset=%PRESET%
if errorlevel 1 (
    echo [build] ERROR: cmake build failed
    exit /b 1
)

echo.
echo [build] OK: %OUTDIR%\dxgi.dll
echo [build] Next: deploy.bat  ^&^&  launcher\start_A.bat
endlocal
