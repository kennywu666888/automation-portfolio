@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
where python.exe >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    where py.exe >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo Python 3 was not found.
    echo Install Python 3.11 or newer from https://www.python.org/downloads/windows/
    echo During setup, enable "Add Python to PATH" or install the Python Launcher.
    pause
    exit /b 1
)

echo Using: %PYTHON_CMD%
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate the virtual environment.
    pause
    exit /b 1
)

echo Installing Python packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Package installation failed. Check the messages above and your network connection.
    pause
    exit /b 1
)

echo Installing Playwright Chromium...
python -m playwright install chromium
if errorlevel 1 (
    echo Chromium installation failed. Run this file again after checking the network.
    pause
    exit /b 1
)

echo.
echo Installation completed. Double-click start.bat to launch the application.
pause
exit /b 0

