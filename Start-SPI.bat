@echo off
setlocal

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-SPI.ps1"

if errorlevel 1 (
    echo.
    echo SPI launcher reported an error.
    echo Check logs in %LOCALAPPDATA%\SPI\runtime.
    pause
)

endlocal
