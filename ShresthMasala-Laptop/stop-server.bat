@echo off
rem =====================================================================
rem  Stop headless servers started by run-browser.bat (--server-only).
rem  Kills ONLY processes carrying the --server-only flag: the desktop
rem  window runs without it and is never touched. Close the desktop app
rem  itself normally if that is what you want to stop.
rem =====================================================================
powershell -NoProfile -Command "$hits = Get-CimInstance Win32_Process -Filter \"Name='ShresthMasalaIndustry.exe'\" | Where-Object { $_.CommandLine -match '--server-only' }; if (-not $hits) { Write-Output 'NONE'; exit 1 }; $hits | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Output ('STOPPED:' + @($hits).Count)"
if errorlevel 1 (
  echo No headless server running. Nothing stopped.
  echo Your desktop app window, if open, is left alone by design.
) else (
  echo Headless server stopped. The browser page will go blank - that is expected.
)
pause
exit /b 0
