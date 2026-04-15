@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Set UTF-8 encoding for CMD
chcp 65001 >nul

title GetGif Starter

echo ==================================================
echo   GetGif Starter
echo ==================================================

:: 1. Detect Virtual Environment
set "PYTHON_EXE=python"

if exist "venv\Scripts\python.exe" (
    echo [VENV] Local 'venv' detected.
    set "PYTHON_EXE=venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    echo [VENV] Local '.venv' detected.
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    echo [VENV] No local venv found, using system python.
)

echo [INFO] Using: %PYTHON_EXE%

:: 2. Check Python
%PYTHON_EXE% --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found or not working.
    pause
    exit /b
)

:: 3. Install Dependencies
echo [INFO] Checking requirements...
%PYTHON_EXE% -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARN] Dependency installation failed.
)

:: 4. Start App
echo [INFO] Starting app.py...
echo [INFO] Backend modules are loaded from src\.
echo [INFO] Do not close this window while using the app.
echo.
%PYTHON_EXE% app.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Application exited with error code %errorlevel%.
    pause
)

endlocal
