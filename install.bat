@echo off
setlocal
title PRO Management Services - Install
cd /d "%~dp0"
echo.
echo ============================================================
echo   PRO Management Services - Desktop - Install
echo   Uses the official, signed Python from python.org,
echo   so Windows Smart App Control allows it.
echo   Needs internet the first time. 3-5 minutes.
echo ============================================================
echo.
set "BASE=%PROGRAMDATA%\PROManagement"
set "PROG=%BASE%\app"
set "VENV=%BASE%\venv"

rem ---- 1) Python 3.12 from python.org ---------------------------------
set "PY="
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
    echo [1/5] Installing Python 3.12 ...
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
)
if not defined PY (
    echo.
    echo  Python 3.12 was installed. CLOSE this window and run install.bat again.
    pause
    exit /b 0
)
echo [1/5] Python:
%PY% --version

rem ---- 2) Close the program and old copies -----------------------------
echo [2/5] Closing the program if it is open...
taskkill /im PROManagement.exe /f >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*PROManagement*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
if exist "%BASE%\program" rmdir /s /q "%BASE%\program" >nul 2>&1

rem ---- 3) Private environment + libraries ------------------------------
echo [3/5] Preparing environment and downloading libraries...
if exist "%VENV%\Scripts\python.exe" (
    "%VENV%\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1 || rmdir /s /q "%VENV%"
)
if not exist "%VENV%\Scripts\python.exe" (
    %PY% -m venv "%VENV%" || goto :fail
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip > "%~dp0install_log.txt" 2>&1
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt >> "%~dp0install_log.txt" 2>&1 || goto :fail

rem ---- 4) Program files -----------------------------------------------
echo [4/5] Copying the program...
robocopy "%~dp0app" "%PROG%" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail
copy /y "%~dp0icon.ico" "%BASE%\icon.ico" >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%BASE%' -Recurse -File -Exclude *.db | Unblock-File" >nul 2>&1

rem ---- 5) Shortcuts ----------------------------------------------------
echo [5/5] Creating the desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$t='%VENV%\Scripts\pythonw.exe'; $a='\"%PROG%\desktop.py\"'; foreach($d in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))){ $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'PRO Management Services.lnk')); $s.TargetPath=$t; $s.Arguments=$a; $s.WorkingDirectory='%PROG%'; $s.IconLocation='%BASE%\icon.ico,0'; $s.Save() }"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo   DONE. The program is on your Desktop: "PRO Management Services"
echo   Data (this computer only): %BASE%\data
echo ============================================================
start "" "%VENV%\Scripts\pythonw.exe" "%PROG%\desktop.py"
timeout /t 5 >nul
exit /b 0

:fail
echo.
echo ************************************************************
echo   Something went wrong. Send Claude the file install_log.txt
echo   from this folder (or a photo of this window).
echo ************************************************************
pause
exit /b 1
