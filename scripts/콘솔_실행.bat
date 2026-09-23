@echo off
rem Launch School Work Console (school_console.py)
rem Installs required modules quietly, then starts the GUI without a black window (pythonw).
cd /d "%~dp0"
python -m pip install -r requirements.txt >nul 2>&1
where pythonw >nul 2>&1 && ( start "" pythonw "school_console.py" ) || ( start "" python "school_console.py" )
exit
