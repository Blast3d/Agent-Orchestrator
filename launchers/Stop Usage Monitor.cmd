@echo off
call "%~dp0..\Run Python.cmd" "%~dp0..\app\portable_setup.py" --quiet
if errorlevel 1 exit /b %errorlevel%
call "%~dp0..\Run Python.cmd" "%~dp0..\app\usage_guard.py" stop-monitor
