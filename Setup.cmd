@echo off
setlocal
call "%~dp0Run Python.cmd" "%~dp0app\portable_setup.py"
if errorlevel 1 exit /b %errorlevel%
call "%~dp0Run Python.cmd" "%~dp0scripts\check_package.py"
if errorlevel 1 pause
