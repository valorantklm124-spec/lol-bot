@echo off
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
    echo [ERR] venv absent. Lance: python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
venv\Scripts\python.exe bot.py
pause
