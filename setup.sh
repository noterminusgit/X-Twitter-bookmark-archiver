#!/bin/bash
# Setup script for Twitter Bookmark Archiver

echo "========================================="
echo "Twitter Bookmark Archiver - Setup"
echo "========================================="
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed."
    echo "Please install Python 3.8 or higher and try again."
    exit 1
fi

echo "Python version:"
python3 --version
echo ""

# Check if pip is installed
if ! command -v pip3 &> /dev/null; then
    echo "Error: pip3 is not installed."
    echo "Please install pip and try again."
    exit 1
fi

echo "Installing Python dependencies..."
pip3 install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "Error: Failed to install Python dependencies."
    exit 1
fi

echo ""
echo "Installing Playwright browsers..."
playwright install chromium

if [ $? -ne 0 ]; then
    echo "Error: Failed to install Playwright browsers."
    exit 1
fi

echo ""
echo "Creating configuration file..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo ".env file created. Please edit it with your Twitter API credentials."
else
    echo ".env file already exists. Skipping..."
fi

echo ""
echo "========================================="
echo "Setup complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Edit .env file with your Twitter API credentials"
echo "2. Run: python3 bookmark_archiver.py"
echo ""
echo "For more information, see README.md"
