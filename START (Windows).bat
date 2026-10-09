@echo off
title AutoEdit - free video editor
cd /d "%~dp0"
rem AutoEdit uses its own copy of Python 3.12 (64-bit), which also runs on ARM laptops.
set "PYX=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -c "import gradio, cv2, faster_whisper" >nul 2>nul && goto run
if exist ".venv" rmdir /s /q .venv
if exist "%PYX%" goto setup
echo Installing Python 3.12 (one time only)...
winget install -e --id Python.Python.3.12 --architecture x64 --scope user --silent --accept-package-agreements --accept-source-agreements
if not exist "%PYX%" goto nopython
:setup
echo First start: setting up AutoEdit (this takes a few minutes, only once)...
"%PYX%" -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" prefetch.py
:run
rem Put an "AutoEdit" shortcut on the Desktop so it can be opened from there.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $l=Join-Path $d 'AutoEdit.lnk'; if(-not (Test-Path $l)){ $s=(New-Object -ComObject WScript.Shell).CreateShortcut($l); $s.TargetPath='%~f0'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%SystemRoot%\System32\imageres.dll,18'; $s.Description='AutoEdit video editor'; $s.Save() }" >nul 2>nul
".venv\Scripts\python.exe" update.py
echo Starting AutoEdit... your browser will open. Keep this window open while you use it.
".venv\Scripts\python.exe" app.py
pause
exit /b
:failed
echo.
echo  Setup did not finish. Check your internet connection and double-click START again.
rmdir /s /q .venv
pause
exit /b
:nopython
echo.
echo  Python could not be installed automatically.
echo  1. A download will start. Run it and click "Install Now".
echo  2. Then double-click this START file again.
start https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
pause
