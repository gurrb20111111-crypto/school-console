@echo off
rem ===================================================================
rem  Debug Chrome launcher for the console (no Python needed).
rem  Opens Chrome with remote-debugging port 9222 and the work sites,
rem  so the console (학교업무콘솔.exe) can attach and monitor them.
rem  Usage: double-click -> log in to each tab -> run the console exe.
rem ===================================================================
set "PROF=%LOCALAPPDATA%\SchoolConsoleProfile"
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

start "" "%CHROME%" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="%PROF%" --no-first-run --no-default-browser-check "https://sen.eduptl.kr/bpm_man_mn00_001.do" "https://klef.sen.go.kr" "https://sen.neis.go.kr/jsp/main.jsp"
exit
