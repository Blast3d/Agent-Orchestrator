@echo off
call "%~dp0Run Python.cmd" "%~dp0app\portable_setup.py" --quiet
if errorlevel 1 exit /b %errorlevel%
call "%~dp0Run Python.cmd" "%~dp0app\start_brain_dashboard.py"
if errorlevel 1 pause
