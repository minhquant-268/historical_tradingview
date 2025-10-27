# PowerShell Script to Disable Quick Edit Mode
param(
    [string]$ExePath = "historical.exe",
    [switch]$NoPause
)

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  TRADINGVIEW HISTORICAL - ANTI-FREEZE LAUNCHER" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

$code = @"
using System;
using System.Runtime.InteropServices;
public class ConsoleUtils {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetStdHandle(int nStdHandle);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
    public const int STD_INPUT_HANDLE = -10;
    public const uint ENABLE_QUICK_EDIT_MODE = 0x0040;
    public const uint ENABLE_EXTENDED_FLAGS = 0x0080;
    public const uint ENABLE_INSERT_MODE = 0x0020;
}
"@

try {
    Add-Type -TypeDefinition $code -Language CSharp
    Write-Host "Disabling Quick Edit Mode..." -ForegroundColor Green
    $consoleHandle = [ConsoleUtils]::GetStdHandle([ConsoleUtils]::STD_INPUT_HANDLE)
    $mode = [uint32]0
    if ([ConsoleUtils]::GetConsoleMode($consoleHandle, [ref]$mode)) {
        Write-Host "Current mode: 0x$($mode.ToString('X8'))" -ForegroundColor Gray
        $newMode = $mode -band (-bnot [ConsoleUtils]::ENABLE_QUICK_EDIT_MODE)
        $newMode = $newMode -band (-bnot [ConsoleUtils]::ENABLE_INSERT_MODE)
        $newMode = $newMode -bor [ConsoleUtils]::ENABLE_EXTENDED_FLAGS
        if ([ConsoleUtils]::SetConsoleMode($consoleHandle, $newMode)) {
            Write-Host "Quick Edit Mode DISABLED" -ForegroundColor Green
            Write-Host "New mode: 0x$($newMode.ToString('X8'))" -ForegroundColor Gray
        }
    }
} catch { Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Starting: $ExePath" -ForegroundColor Green
Write-Host ""
if (-not (Test-Path $ExePath)) {
    # Try common fallback location inside dist\historical
    $fallback = Join-Path $PSScriptRoot "dist\historical\historical.exe"
    if (Test-Path $fallback) {
        Write-Host "Found fallback executable: $fallback" -ForegroundColor Yellow
        $ExePath = $fallback
    }
}

if (Test-Path $ExePath) {
    & $ExePath
} else {
    Write-Host "File not found: $ExePath" -ForegroundColor Red
}
Write-Host ""
if (-not $NoPause) {
    Write-Host "Press any key to exit..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
