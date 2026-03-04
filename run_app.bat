@echo off
title Nikke Union Raid Planner
echo.
echo  ==========================================
echo   Nikke Union Raid Hard Mode Planner
echo  ==========================================
echo.

:: Try miniconda python first, then fall back to system python
set PYTHON=
if exist "%USERPROFILE%\miniconda3\python.exe" (
    set PYTHON=%USERPROFILE%\miniconda3\python.exe
) else if exist "%USERPROFILE%\anaconda3\python.exe" (
    set PYTHON=%USERPROFILE%\anaconda3\python.exe
) else (
    set PYTHON=python
)

echo  Using Python: %PYTHON%
echo.

:: Install all dependencies from requirements.txt
echo  Installing dependencies...
"%PYTHON%" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo  [!] Failed to install dependencies. Check requirements.txt and your Python environment.
    pause
    exit /b 1
)
echo  Dependencies OK.
echo.

echo  Starting app at http://localhost:8501
echo  Press Ctrl+C to stop.
echo.

"%PYTHON%" -m streamlit run app.py --server.headless false --browser.gatherUsageStats false

pause
