@echo off
cd /d "%~dp0"
rem Nur EIN Python starten: erst den Windows-Starter "py", sonst "python".
rem (Frueher startete "py ... || python ..." eine zweite Instanz, sobald die erste mit Fehlercode endete.)
where py >nul 2>nul
if %errorlevel%==0 (
  py app.py
) else (
  python app.py
)
pause
