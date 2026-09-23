@echo off
title 세션유지 크롬 프로필 최초 설정

set "PROFILE=%LOCALAPPDATA%\SenKeepAliveProfile"

set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

echo ============================================================
if not defined CHROME (
  echo [오류] 크롬 실행파일을 찾을 수 없습니다.
  echo  크롬이 다른 위치에 설치돼 있을 수 있어요.
  echo ============================================================
  pause
  exit /b
)

echo  찾은 크롬: %CHROME%
echo.
echo  새 크롬 창이 열립니다.
echo    1) "Chrome에 로그인하세요" 화면 -- [로그아웃 상태 유지] 클릭
echo    2) 그 크롬 창을 닫기
echo ============================================================
echo.

start "" "%CHROME%" --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check "https://sen.eduptl.kr"

echo  크롬을 실행했습니다.
echo  (혹시 안 보이면 다른 모니터나 다른 창 뒤에 있는지 확인하세요)
echo.
echo  위 1~2 를 마친 뒤, 이 창에서 아무 키나 누르면 닫힙니다.
pause
