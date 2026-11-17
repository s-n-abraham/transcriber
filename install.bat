@echo off
rem One-time setup for Transcriber on Windows 10/11. Safe to run again.
rem   install.bat            full setup (asks before downloading the speech model)
rem   install.bat nopause    used internally by Transcriber.bat
setlocal
cd /d "%~dp0"
echo.
echo === Transcriber setup ===
echo.

rem ---- 1. find Python (3.10 - 3.13 recommended)
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( where py >nul 2>nul && set "PY=py -3" )
if not defined PY (
  echo Python was not found.
  where winget >nul 2>nul
  if errorlevel 1 (
    echo Please install Python 3.12 from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
    echo then run install.bat again.
    goto :fail
  )
  echo Installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python installed. Please CLOSE this window and run install.bat again.
  goto :fail
)

rem ---- 2. virtual environment + packages
if not exist ".venv\Scripts\python.exe" (
  echo Creating environment...
  %PY% -m venv .venv || goto :fail
)
echo Installing packages ^(this takes a few minutes the first time^)...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :fail

rem ---- 3. desktop shortcut (set TRANSCRIBER_NO_SHORTCUT=1 to skip)
if defined TRANSCRIBER_NO_SHORTCUT goto :afterlink
powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Transcriber.lnk'); $s.TargetPath='%~dp0Transcriber.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Description='Transcribe videos to readable text'; $s.Save()" 2>nul
if not errorlevel 1 echo Desktop shortcut created: Transcriber

:afterlink
rem ---- 4. optional: download the default speech model now (about 465 MB, one time)
if /i "%~1"=="nopause" goto :done
echo.
echo The first transcription needs the speech model ^(about 465 MB, downloaded once, then works offline^).
choice /c YN /t 20 /d N /m "Download it now"
if errorlevel 2 goto :done
".venv\Scripts\python.exe" -c "from faster_whisper import download_model; print('Downloading...'); download_model('small.en'); print('Done.')"

:done
echo.
echo All set. Start it with the Transcriber desktop shortcut, or by double-clicking Transcriber.bat.
if /i not "%~1"=="nopause" pause
exit /b 0

:fail
echo.
echo Setup did not finish.
if /i not "%~1"=="nopause" pause
exit /b 1
