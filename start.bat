@echo off
rem Double-click to start the remote server (Windows).
cd /d "%~dp0"
title Better Roku Remote
where py >nul 2>nul && (py -3 roku_remote.py & goto done)
where python >nul 2>nul && (python roku_remote.py & goto done)
echo Python 3 isn't installed.
echo Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" during setup.
:done
echo.
pause
