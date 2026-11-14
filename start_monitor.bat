@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 monitor.py --config config.json --watch
) else (
    python monitor.py --config config.json --watch
)
pause
