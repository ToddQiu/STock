@echo off
powershell.exe -NoProfile -File "%~dp0Launch-STock.ps1"
if errorlevel 1 echo Deployment stopped. Read the error above before retrying.
pause
