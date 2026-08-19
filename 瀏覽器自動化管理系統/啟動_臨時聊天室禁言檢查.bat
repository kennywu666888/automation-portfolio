@echo off
setlocal
cd /d "%~dp0"
python "temp_messenger_mute_checker_launcher.py" %*
if errorlevel 1 pause
endlocal
