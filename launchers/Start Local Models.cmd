@echo off
setlocal
call "%~dp0..\Run Python.cmd" "%~dp0..\app\portable_setup.py" --quiet
if errorlevel 1 exit /b %errorlevel%
call "%~dp0..\Run Python.cmd" "%~dp0..\app\manage_local.py" start
if errorlevel 1 pause
