@echo off
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo Telegram Proxy Checker - Purple UI Launcher
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python detected.
echo.
echo Installing dependencies...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

echo Dependencies installed.
echo.
echo Starting Telegram Proxy Checker GUI...
echo.
python proxy_gui.py
if errorlevel 1 (
    echo.
    echo GUI failed to start. Running CLI fallback...
    python telegram_proxy_checker.py -i proxies.txt -o working.txt -t 5 -c 300 --strict
)

echo.
echo Finished.
pause

endlocal
