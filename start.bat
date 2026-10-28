@echo off
cd /d "%~dp0"
set "APP_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "APP_PYTHON=%~dp0.venv\Scripts\python.exe"
"%APP_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
    pause
    exit /b 1
)
"%APP_PYTHON%" -m streamlit run app.py
pause
