@echo off
rem =====================================================================
rem  Shresth Masala Industry - Management System : App launcher
rem  Opens the desktop app window directly - no browser, no visible server
rem  console. Use run-server.bat instead if you prefer the browser.
rem
rem  Usage:  run-app.bat [--help]
rem
rem  What it does:
rem    * Next to ShresthMasalaIndustry.exe: starts the exe.
rem    * In the source folder: starts main_window.py with pythonw, so no
rem      black console window stays on screen.
rem
rem  First run ever: click Register, create the admin account, sign in.
rem  To STOP: just close the app window.
rem =====================================================================
if "%1"=="--help" goto :help
if "%1"=="-h" goto :help
if "%1"=="/?" goto :help
cd /d "%~dp0"
if exist "ShresthMasalaIndustry.exe" (
  start "" "ShresthMasalaIndustry.exe"
  exit /b 0
)
if not exist "main_window.py" (
  echo [ERROR] Nothing to start here - need ShresthMasalaIndustry.exe
  echo or main_window.py in this folder. Re-copy the app folder.
  pause
  exit /b 1
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "ShresthMasala" pythonw main_window.py
  exit /b 0
)
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found.
  echo Install free Python 3.12+ from https://www.python.org/downloads/
  echo In the installer, tick Add python.exe to PATH, then retry.
  pause
  exit /b 1
)
echo Opening Shresth Masala Industry - a console window stays while it runs.
start "ShresthMasala" python main_window.py
exit /b 0
:help
echo Shresth Masala Industry - App launcher
echo.
echo   Opens the desktop app window directly: exe if present, else
echo   main_window.py without a console window when pythonw exists.
echo.
echo   run-app.bat [--help]   show this help
echo   run-app.bat            open the app
echo.
echo First run: click Register, create the admin account.
echo Stop: close the app window.
exit /b 0
