@echo off
REM Setup script for Twitter Bookmark Archiver (Windows)

echo =========================================
echo Twitter Bookmark Archiver - Setup
echo =========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed.
    echo Please install Python 3.8 or higher and try again.
    pause
    exit /b 1
)

echo Python version:
python --version
echo.

REM Check if pip is installed
pip --version >nul 2>&1
if errorlevel 1 (
    echo Error: pip is not installed.
    echo Please install pip and try again.
    pause
    exit /b 1
)

echo Installing Python dependencies...
pip install -r requirements.txt

if errorlevel 1 (
    echo Error: Failed to install Python dependencies.
    pause
    exit /b 1
)

echo.
echo Installing Playwright browsers...
playwright install chromium

if errorlevel 1 (
    echo Error: Failed to install Playwright browsers.
    pause
    exit /b 1
)

echo.
echo Creating configuration file...
if not exist .env (
    copy .env.example .env
    echo .env file created. Please edit it with your Twitter API credentials.
) else (
    echo .env file already exists. Skipping...
)

echo.
echo =========================================
echo Setup complete!
echo =========================================
echo.
echo Next steps:
echo 1. Edit .env file with your Twitter API credentials
echo 2. Run: python bookmark_archiver.py
echo.
echo For more information, see README.md
echo.
pause
