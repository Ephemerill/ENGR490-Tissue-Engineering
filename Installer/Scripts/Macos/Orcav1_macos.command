#!/bin/bash

echo "Starting Orca Setup and Execution..."
echo "------------------------------------"

# 1. Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed."
    echo "Please download and install the latest macOS release from: https://www.python.org/downloads/mac-osx/"
    echo "Press any key to exit..."
    read -n 1
    exit 1
fi

# 2. Check for Git
if ! command -v git &> /dev/null; then
    echo "Error: Git is not installed."
    echo "Please install it by opening a new terminal window and typing: xcode-select --install"
    echo "Alternatively, download it from: https://git-scm.com/download/mac"
    echo "Press any key to exit..."
    read -n 1
    exit 1
fi

# Configuration
REPO_URL="https://github.com/Ephemerill/ENGR490-Tissue-Engineering.git"
DIR_NAME="ENGR490-Tissue-Engineering"

# 3. Pull updates or clone the original repository
if [ -d "$DIR_NAME" ]; then
    echo "Found existing repository. Pulling the latest updates..."
    cd "$DIR_NAME" || exit
    git pull
else
    echo "Repository not found. Cloning for the first time..."
    git clone "$REPO_URL"
    cd "$DIR_NAME" || exit
fi

# 4. Make the virtual environment
echo "Checking virtual environment..."
if [ ! -d "venv" ]; then
    echo "Creating new virtual environment..."
    python3 -m venv venv
fi

# Activate the virtual environment
source venv/bin/activate

# 5. Install dependencies
echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt

# 6. Run the Python file
echo "------------------------------------"
echo "Launching Orca..."
python3 orca.py

echo "------------------------------------"
echo "Process finished."