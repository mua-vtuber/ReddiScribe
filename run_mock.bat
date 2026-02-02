@echo off
chcp 65001 >nul 2>&1
title ReddiScribe (Mock Mode)

echo ============================================
echo   ReddiScribe - MOCK MODE (no network)
echo ============================================
echo.

REM Try python from PATH first, then common locations
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=python
    goto :check_deps
)

if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
    set PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python313\python.exe
    goto :check_deps
)

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
    goto :check_deps
)

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
    goto :check_deps
)

echo [ERROR] Python not found. Install Python 3.11+ from https://www.python.org
pause
exit /b 1

:check_deps
echo Using: %PYTHON_CMD%
echo.

REM Check if PyQt6 is installed
%PYTHON_CMD% -c "import PyQt6" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Installing dependencies...
    %PYTHON_CMD% -m pip install -e "%~dp0." --quiet
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    echo Dependencies installed.
    echo.
)

REM Enable mock mode in config before launching
echo Enabling mock mode...
%PYTHON_CMD% -c "
import sys; sys.path.insert(0, '.')
from src.core.config_manager import ConfigManager
config = ConfigManager()
config.set('reddit.mock_mode', True)
config.save()
print('Mock mode enabled.')
"

echo Starting ReddiScribe (Mock Mode)...
echo.
cd /d "%~dp0"
%PYTHON_CMD% -m src.main

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] ReddiScribe exited with error code %ERRORLEVEL%
    pause
)
