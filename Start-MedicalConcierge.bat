@echo off
setlocal EnableDelayedExpansion
title Medical Concierge
cd /d "%~dp0"

echo.
echo  ============================================
echo    Medical Concierge - starting up
echo  ============================================
echo.

rem --- 1. Find Python (py launcher first, then python on PATH) ---
set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON (
    where python >nul 2>nul && set "PYTHON=python"
)
if not defined PYTHON (
    echo  Python was not found on this computer.
    echo.
    echo  1. Go to  https://www.python.org/downloads/
    echo  2. Click "Download Python" and run the installer.
    echo  3. IMPORTANT: tick the box "Add python.exe to PATH".
    echo  4. When it finishes, double-click this file again.
    echo.
    pause
    exit /b 1
)

rem --- 2. Create the private Python environment on first run ---
if not exist ".venv\Scripts\python.exe" (
    echo  First-time setup: preparing the app ^(about a minute^)...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo  Could not set up the Python environment. See the message above.
        pause
        exit /b 1
    )
)

rem --- 3. Install/update dependencies when requirements change ---
fc /b backend\requirements.txt .venv\installed.txt >nul 2>nul
if errorlevel 1 (
    echo  Installing components ^(first run only^)...
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r backend\requirements.txt
    if errorlevel 1 (
        echo  Installing components failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
    copy /y backend\requirements.txt .venv\installed.txt >nul
)

rem --- 4. First-run: ask for the Anthropic API key ---
if not exist "backend\.env" (
    echo.
    echo  One-time setup: the app needs an Anthropic API key to read documents.
    echo  Get one at  https://console.anthropic.com  ^(Settings ^> API keys^).
    echo  You can also press Enter to skip for now and add it later.
    echo.
    set "APIKEY="
    set /p APIKEY="  Paste your API key here and press Enter: "
    (
        echo ANTHROPIC_API_KEY=!APIKEY!
        echo EXTRACTION_MODEL=claude-sonnet-5
        echo RXNORM_BASE_URL=https://rxnav.nlm.nih.gov/REST
        echo DB_PATH=./medconcierge.sqlite3
        echo REVIEW_CONFIDENCE_THRESHOLD=0.6
        echo PDF_RENDER_DPI=200
    ) > "backend\.env"
    echo.
)

rem --- 5. Start the app and open the browser ---
echo  Starting Medical Concierge at http://localhost:8000
echo.
echo  Leave this window open while you use the app.
echo  Close this window when you are done - your data is saved automatically.
echo.
start "" "http://localhost:8000"
cd backend
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo  Medical Concierge has stopped. You can close this window.
pause >nul
