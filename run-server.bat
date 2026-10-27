@echo off
rem =====================================================================
rem  Shresth Masala Industry - Management System : Dev Server launcher
rem  100%% free stack (Python + SQLite). No licence needed for anything here.
rem
rem  Usage:  run-server.bat [--help]
rem          Optional env var:  set PORT=8001  (default 8000)
rem
rem  What it does:
rem    * Next to ShresthMasalaIndustry.exe (built app folder): starts the
rem      exe AND opens the browser at its login page.
rem    * In the source folder (backend\main.py present): starts the dev
rem      server on http://127.0.0.1:PORT/ (this PC only) + opens the browser.
rem
rem  First run ever: the database is empty, so click Register and create
rem  your admin account, then sign in.
rem
rem  To STOP a dev server: close the ShresthMasala server window (Ctrl+C in it).
rem  If the port is busy: close the other copy, or run with another port:
rem      set PORT=8001 ^& run-server.bat
rem =====================================================================
if "%1"=="--help" goto :help
if "%1"=="-h" goto :help
if "%1"=="/?" goto :help
if "%PORT%"=="" set PORT=8000
cd /d "%~dp0"
if exist "ShresthMasalaIndustry.exe" (
  echo Starting Shresth Masala Industry desktop app + browser login...
  start "" "ShresthMasalaIndustry.exe"
  timeout /t 8 /nobreak >nul
  start "" "http://127.0.0.1:8000/"
  echo App window opening. Browser opens the login page at http://127.0.0.1:8000/
  echo If the browser shows another copy instead, close other servers first,
  echo because the app takes the next free port from 8000-8005.
  exit /b 0
)
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found.
  echo Install free Python 3.12+ from https://www.python.org/downloads/
  echo In the installer, tick "Add python.exe to PATH", then double-click this file again.
  pause
  exit /b 1
)
if not exist "backend\main.py" (
  echo [ERROR] backend\main.py not found. Keep this file inside the app folder.
  pause
  exit /b 1
)
echo Starting Shresth Masala Industry on http://127.0.0.1:%PORT%/ ...
echo A server window opens - leave it running while you use the app.
start "ShresthMasala Server" cmd /k python -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT%
call :waitport
if errorlevel 1 (
  echo [ERROR] No answer on http://127.0.0.1:%PORT%/ after ~60 seconds.
  echo Look at the ShresthMasala Server window for the real error,
  echo then close it and double-click this file again.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:%PORT%/"
echo Browser opened. To STOP the server, close the ShresthMasala Server window.
pause
exit /b 0
:waitport
powershell -NoProfile -Command "$ok=$false; for ($i=0; $i -lt 120; $i++) { try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', %PORT%); $c.Close(); $ok=$true; break } catch { Start-Sleep -Milliseconds 500 } }; if ($ok) { exit 0 } else { exit 1 }"
exit /b %errorlevel%
:help
echo Shresth Masala Industry - launcher
echo.
echo   Next to ShresthMasalaIndustry.exe:  starts the desktop app + browser.
echo   In the source folder:               starts the dev server + browser.
echo.
echo   run-server.bat [--help]        show this help
echo   run-server.bat                 start (port 8000)
echo   set PORT=8001 ^& run-server.bat  use another port if 8000 is busy
echo.
echo First run: click Register in the app, create the admin account.
echo Stop a dev server: close the ShresthMasala server window (or Ctrl+C inside it).
echo Never expose the port beyond 127.0.0.1 - the data has no login wall.
exit /b 0
