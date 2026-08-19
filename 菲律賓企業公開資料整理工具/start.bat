@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The virtual environment is not installed.
    echo Double-click install.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate the virtual environment.
    echo Run install.bat again.
    pause
    exit /b 1
)

python -c "import PyQt6, requests, bs4, pandas, openpyxl, dotenv" >nul 2>nul
if errorlevel 1 (
    echo Required packages are missing or damaged.
    echo Run install.bat again.
    pause
    exit /b 1
)

python main.py
if errorlevel 1 (
    echo The application ended with an error. Check the logs folder.
    pause
    exit /b 1
)
exit /b 0
