@echo off
rem Double-click to open the Transcriber window (sets itself up on first run).
rem Command line:  Transcriber.bat "https://youtu.be/..." --model small.en
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call install.bat nopause || exit /b 1
)
if "%~1"=="" (
  start "" ".venv\Scripts\pythonw.exe" app.py
) else (
  ".venv\Scripts\python.exe" app.py %*
  pause
)
exit /b 0
