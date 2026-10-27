@echo off
rem =====================================================================
rem  Shresth Masala Industry - open the running app in the browser.
rem  Browser only: finds the server on ports 8000-8005 and opens it.
rem  It starts nothing - use run-server.bat for the one-click
rem  server-plus-browser launch.
rem
rem  Usage:  run-browser.bat [--help]
rem =====================================================================
if "%1"=="--help" goto :help
if "%1"=="-h" goto :help
if "%1"=="/?" goto :help
set SERVPORT=
for /f "delims=" %%P in ('powershell -NoProfile -Command "foreach ($p in 8000,8001,8002,8003,8004,8005) { try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',$p); $c.Close(); Write-Output $p; exit 0 } catch { } }"') do set SERVPORT=%%P
if "%SERVPORT%"=="" (
  echo [ERROR] No app answering on ports 8000-8005.
  echo Start it first with run-server.bat, then run this file again.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:%SERVPORT%/"
exit /b 0
:help
echo Shresth Masala Industry - browser shortcut
echo.
echo   Opens the running app in your browser. That is all it does:
echo   start the app first with run-server.bat, run-app.bat, or the exe.
echo.
echo   run-browser.bat [--help]   show this help
exit /b 0
