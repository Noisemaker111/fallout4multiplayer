@echo off
echo ============================================================
echo  FO4 1.10.163 downgrade - paste these into Steam CONSOLE
echo  (Steam already opened with steam://open/console)
echo ============================================================
echo.
echo download_depot 377160 377162 5847529232406005096
echo download_depot 377160 377161 7497069378349273908
echo download_depot 377160 377163 5819088023757897745
echo download_depot 377160 377164 2178106366609958945
echo.
echo After downloads say "Depot download complete", run:
echo   powershell -ExecutionPolicy Bypass -File "%~dp0apply_fo4_163_downgrade.ps1"
echo.
pause
