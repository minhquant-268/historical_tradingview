@echo off
REM Ensure we run from the script folder (fixes Task Scheduler working directory issue)
pushd "%~dp0"

REM Simple log for scheduled runs
set "LAUNCHER_LOG=%~dp0run_launcher.log"
echo ========================= %DATE% %TIME% =========================>> "%LAUNCHER_LOG%"
echo User: %USERNAME%  Cwd: %CD% >> "%LAUNCHER_LOG%"
REM =====================================================
REM TradingView RealTime Launcher - Anti-Freeze Version
REM This script disables Quick Edit Mode before running
REM =====================================================

echo.
echo ====================================================
echo   TRADINGVIEW HISTORICAL - ANTI-FREEZE LAUNCHER
echo ====================================================
echo.

REM Check if PowerShell is available (use full path for scheduled tasks)
set "POWERSHELL=%SystemRoot%\system32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL%" (
    where powershell >nul 2>nul
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] PowerShell is not available! >> "%LAUNCHER_LOG%"
        echo [ERROR] PowerShell is not available!
        echo [ERROR] Cannot disable Quick Edit Mode >> "%LAUNCHER_LOG%"
        goto :FALLBACK
    ) else (
        set "POWERSHELL=powershell"
    )
)
    echo [ERROR] PowerShell is not available!
    echo [ERROR] Cannot disable Quick Edit Mode
    echo.
    echo Falling back to direct execution... >> "%LAUNCHER_LOG%"
    if exist "historical.exe" (
        echo Running historical.exe directly >> "%LAUNCHER_LOG%"
        historical.exe
    ) else if exist "dist\historical\historical.exe" (
        echo Running dist\historical\historical.exe >> "%LAUNCHER_LOG%"
        cd dist\historical
        historical.exe
    ) else (
        echo [ERROR] historical.exe not found! >> "%LAUNCHER_LOG%"
        echo [ERROR] historical.exe not found!
    )
    rem Do not pause here (Task Scheduler should not be interactive)
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
    echo [ERROR] Cannot find historical.exe! >> "%LAUNCHER_LOG%"
    echo [ERROR] Cannot find historical.exe!
    echo.
    echo Searched in: >> "%LAUNCHER_LOG%"
    echo   - %WORK_DIR%historical.exe >> "%LAUNCHER_LOG%"
    echo   - %WORK_DIR%dist\historical\historical.exe >> "%LAUNCHER_LOG%"
    echo.
    echo Please build the application first using: >> "%LAUNCHER_LOG%"
    echo   historical-build.bat >> "%LAUNCHER_LOG%"
    echo.
    rem No interactive pause for scheduled runs
    exit /b 1
)

echo Found executable: %EXE_PATH% >> "%LAUNCHER_LOG%"
echo Found executable: %EXE_PATH%
echo.
echo Launching with Quick Edit Mode DISABLED... >> "%LAUNCHER_LOG%"
echo Launching with Quick Edit Mode DISABLED...

REM Run PowerShell script to disable Quick Edit and start app (no pause)
"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0disable_quickedit.ps1" -ExePath "%EXE_PATH%" -NoPause >> "%LAUNCHER_LOG%" 2>&1

echo Application closed. >> "%LAUNCHER_LOG%"
echo Application closed.
rem No interactive pause here
popd
