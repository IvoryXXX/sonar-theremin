@echo off
cd /d "%~dp0radar-scan"
where pio >nul 2>nul
if errorlevel 1 (
    echo PlatformIO neni v PATH.
    echo 1. Otevri VS Code nebo Cursor
    echo 2. Nainstaluj rozsireni "PlatformIO IDE"
    echo 3. Otevri slozku radar-scan
    echo 4. Dole klikni Upload (sipka)
    pause
    exit /b 1
)
pio run -t upload --upload-port COM7
if errorlevel 1 pause
