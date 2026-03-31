@echo off
echo Starting Orca Setup and Execution...
echo ------------------------------------

:: 1. Check for Python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Error: Python is not installed or not added to your system PATH.
    echo Please download it from: https://www.python.org/downloads/windows/
    echo.
    echo *** CRITICAL: During installation, you MUST check the box at the bottom that says "Add Python.exe to PATH" ***
    echo.
    pause
    exit /b 1
)

:: 2. Check for Git
git --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Error: Git is not installed.
    echo Please download and install it from: https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

:: Configuration
set REPO_URL=https://github.com/Ephemerill/ENGR490-Tissue-Engineering.git
set DIR_NAME=ENGR490-Tissue-Engineering

:: 3. Pull updates or clone the original repository
IF EXIST "%DIR_NAME%\" (
    echo Found existing repository. Pulling the latest updates...
    cd "%DIR_NAME%"
    git pull
) ELSE (
    echo Repository not found. Cloning for the first time...
    git clone "%REPO_URL%"
    cd "%DIR_NAME%"
)

:: 4. Make the virtual environment
echo Checking virtual environment...
IF NOT EXIST "venv\" (
    echo Creating new virtual environment...
    python -m venv venv
)

:: Activate the virtual environment
call venv\Scripts\activate.bat

:: 5. Install dependencies
echo Installing dependencies from requirements.txt...
pip install -r requirements.txt

:: 6. Run the Python file
echo ------------------------------------
echo Launching Orca...
python orca.py

echo ------------------------------------
echo Process finished.
pause