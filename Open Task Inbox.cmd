@echo off
call "%~dp0Run Python.cmd" "%~dp0app\portable_setup.py" --quiet
if errorlevel 1 exit /b %errorlevel%
call "%~dp0Run Python.cmd" "%~dp0orchestrator.py" inbox --open
if errorlevel 1 pause
