@echo off
setlocal
cd /d "%~dp0\.."
echo =====================================================
echo   UG-DCMS Setup.exe Builder
echo =====================================================
echo.
PowerShell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\installer\Build-Setup.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :failed
echo.
echo [OK] Setup.exe build completed.
echo Output: installer\output\UG-DCMS-Setup-1.0.0-rc2.36.exe
if exist ".\installer\output" start "" ".\installer\output"
pause
exit /b 0

:failed
echo.
echo [FAILED] Setup.exe build failed. Exit code: %RC%
echo Please review the PowerShell error shown above.
pause
exit /b %RC%
