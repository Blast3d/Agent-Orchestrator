@echo off
call "%~dp0..\Run Python.cmd" "%~dp0..\app\portable_setup.py" --quiet
if errorlevel 1 exit /b %errorlevel%
call "%~dp0..\Run Python.cmd" "%~dp0..\app\start_usage_monitor.py"
