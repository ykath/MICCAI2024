@echo off
setlocal

cd /d "%~dp0"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8766"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found in PATH.
  echo Please install Python or add it to PATH, then run this script again.
  pause
  exit /b 1
)

if not exist "browse_papers.py" (
  echo [ERROR] browse_papers.py was not found in:
  echo %CD%
  pause
  exit /b 1
)

if not exist "data\miccai2024.sqlite" (
  echo [ERROR] Database was not found: data\miccai2024.sqlite
  pause
  exit /b 1
)

echo Starting MICCAI 2024 Paper Browser...
echo URL: http://127.0.0.1:%PORT%/
echo.
echo Press Ctrl+C to stop the service.
echo.

start "" "http://127.0.0.1:%PORT%/"
python browse_papers.py --port %PORT%

pause
