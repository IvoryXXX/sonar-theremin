@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtualenv not found. Run: python -m venv .venv ^&^& .venv\Scripts\python -m pip install -e .
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m theremin --sampler
if errorlevel 1 pause
