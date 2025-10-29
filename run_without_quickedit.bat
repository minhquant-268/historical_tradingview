@echo off
REM =====================================================
REM TradingView Historical Launcher - Anti-Freeze Version
REM This script disables Quick Edit Mode before running
REM =====================================================

echo.
echo ====================================================
echo   TRADINGVIEW HISTORICAL - ANTI-FREEZE LAUNCHER
echo ====================================================
echo.

REM Check if PowerShell is available
where powershell >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PowerShell is not available!
    echo [ERROR] Cannot disable Quick Edit Mode
    echo.
    echo Falling back to direct execution...
    if exist "historical.exe" (
        historical.exe
    ) else if exist "dist\historical\historical.exe" (
        cd dist\historical
        historical.exe
    ) else (
        echo [ERROR] historical.exe not found!
    )
    pause
    exit /b 1
)

REM Find the executable
set "EXE_PATH="
set "WORK_DIR=%~dp0"

if exist "%WORK_DIR%historical.exe" (
    set "EXE_PATH=%WORK_DIR%historical.exe"
) else if exist "%WORK_DIR%dist\historical\historical.exe" (
    set "EXE_PATH=%WORK_DIR%dist\historical\historical.exe"
)

if not defined EXE_PATH (
    echo [ERROR] Cannot find historical.exe!
    echo.
    echo Searched in:
    echo   - %WORK_DIR%historical.exe
    echo   - %WORK_DIR%dist\historical\historical.exe
    echo.
    echo Please build the application first using:
    echo   historical-build.bat
    echo.
    pause
    exit /b 1
)

echo Found executable: %EXE_PATH%
echo.
echo Launching with Quick Edit Mode DISABLED...
echo.

REM Run PowerShell script to disable Quick Edit and start app
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0disable_quickedit.ps1" -ExePath "%EXE_PATH%"

echo.
echo Application closed.
pause
