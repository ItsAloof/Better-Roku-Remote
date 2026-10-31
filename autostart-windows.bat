@echo off
rem Double-click to turn autostart ON (or OFF if it's already on).
rem When on, the remote starts hidden in the background every time you log in to Windows.
setlocal
cd /d "%~dp0"
set "VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Better Roku Remote.vbs"

if exist "%VBS%" (
  del "%VBS%"
  powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*roku_remote.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
  echo Autostart is now OFF and the background remote has stopped.
  goto done
)

set "PY="
where pyw >nul 2>nul && set "PY=pyw -3"
if not defined PY (where pythonw >nul 2>nul && set "PY=pythonw")
if not defined PY (
  echo Python 3 isn't installed.
  echo Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" during setup.
  goto done
)

> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%VBS%" echo sh.CurrentDirectory = "%~dp0."
>> "%VBS%" echo sh.Run "%PY% roku_remote.py", 0, False

start "" wscript "%VBS%"
echo Autostart is now ON. The remote is running in the background and will start
echo by itself whenever you log in. Double-click this file again to turn it off.
echo.
echo If Windows asks whether Python can use the network, allow it on Private networks.
:done
echo.
pause
