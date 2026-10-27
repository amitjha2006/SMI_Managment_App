@echo off
REM Testing environment using masala_factory_24_oct.db as primary
REM Run from: C:\Shresth_Masala_Application_New

set APP_ENV=test
set DATABASE_URL=sqlite:///C:\Shresth_Masala_Application_New\ShresthMasala-Laptop\data\masala_factory.db
set PYTHONPATH=C:\Shresth_Masala_Application_New

echo Starting test server on port 8002...
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8002 --app-dir C:\Shresth_Masala_Application_New