@echo off
REM Build + run the native-side unit tests.
REM
REM These cover only engine-independent logic (currently ghost_registry).
REM Anything that touches the FO4 scene graph or a hooked address cannot be
REM tested off-target and needs the game — see docs/MULTIPEER.md.
REM
REM Toolchain discovery mirrors ..\build.bat.

setlocal EnableExtensions EnableDelayedExpansion

set "VCVARS="
if defined FW_VCVARS if exist "%FW_VCVARS%" set "VCVARS=%FW_VCVARS%"

REM vswhere lives at a fixed path under "Program Files (x86)". The "(x86)" parens
REM are why this runs OUTSIDE any if-block: inside one, cmd matches that ")" as
REM the block terminator and the line falls apart.
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
REM Redirect to a temp file rather than using `for /f "usebackq"`: when the
REM command inside the backticks starts with a quoted path, cmd strips the
REM outer quotes and then splits "Program Files (x86)" on the space.
if defined VCVARS goto :have_vcvars
if not exist "%VSWHERE%" goto :have_vcvars
"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath > "%TEMP%\fw_vswhere.txt" 2>nul
if not exist "%TEMP%\fw_vswhere.txt" goto :have_vcvars
set /p VSINSTALL=<"%TEMP%\fw_vswhere.txt"
del "%TEMP%\fw_vswhere.txt" >nul 2>&1
if defined VSINSTALL if exist "%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat"
:have_vcvars
if not defined VCVARS (
    if exist "E:\BuildTools\VC\Auxiliary\Build\vcvars64.bat" (
        set "VCVARS=E:\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    )
)
if not defined VCVARS (
    echo [test] ERROR: no MSVC x64 toolchain found.
    exit /b 1
)

call "%VCVARS%" >nul
if errorlevel 1 exit /b 1

cd /d "%~dp0"
if not exist "out" mkdir out

REM log.cpp supplies FW_LOG; it only needs windows.h, so the registry links
REM standalone without pulling in any hook or scene-graph translation unit.
cl /nologo /std:c++20 /EHsc /W4 /permissive- /Zc:__cplusplus /utf-8 ^
   /DWIN32_LEAN_AND_MEAN /DNOMINMAX /D_CRT_SECURE_NO_WARNINGS ^
   /Fo:out\ /Fe:out\test_ghost_registry.exe ^
   test_ghost_registry.cpp ..\src\native\ghost_registry.cpp ..\src\log.cpp ^
   kernel32.lib user32.lib
if errorlevel 1 (
    echo [test] ERROR: compile failed
    exit /b 1
)

echo.
out\test_ghost_registry.exe
if errorlevel 1 (
    echo.
    echo [test] FAILED
    exit /b 1
)

echo [test] OK
endlocal
