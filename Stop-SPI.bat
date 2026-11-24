@echo off
setlocal

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Stop-SPI.ps1"

if errorlevel 1 (
    echo.
    echo SPI shutdown reported an error.
    pause
)

endlocal
