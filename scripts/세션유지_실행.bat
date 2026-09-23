@echo off
title NEIS / Session Keeper
cd /d "%~dp0"
echo Checking / installing required modules...
python -m pip install -r requirements.txt
echo.
echo Starting session keeper. (Keep this window open)
python session_keeper.py
pause
