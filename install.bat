@echo off
rem One-time setup for Transcriber on Windows 10/11. Safe to run again.
rem   install.bat            full setup (asks before downloading the speech model)
rem   install.bat nopause    used internally by Transcriber.bat
setlocal
cd /d "%~dp0"
echo.
echo === Transcriber setup ===
echo.

rem ---- 1. find a WORKING Python 3.10+ (the Microsoft Store "python.exe" stub does not count)
set "PY="
call :trypy "py -3"
call :trypy python
call :trypy python3
if defined PY goto :havepy

echo No working Python was found on this PC.
echo ^(If you see a Microsoft Store message about "python", that is only a placeholder, not Python.^)
where winget >nul 2>nul
if errorlevel 1 (
  echo.
  echo Please install Python 3.12 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH", then run install.bat again.
  goto :fail
)
echo.
echo Installing Python 3.12 with winget ^(about 30 MB^)...
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
set "PYX=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%PYX%" (
  set PY="%PYX%"
  goto :havepy
)
call :trypy "py -3"
if defined PY goto :havepy
echo.
echo Python was installed, but this window cannot see it yet.
echo Please CLOSE this window and run install.bat again.
goto :fail

:havepy
echo Using Python: %PY%

rem ---- 2. virtual environment + packages
if exist ".venv" if not exist ".venv\Scripts\python.exe" rmdir /s /q ".venv"
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

:trypy
rem usage: call :trypy "<command>"  - sets PY if the command runs and is Python 3.10 or newer
if defined PY exit /b 0
%~1 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b 0
