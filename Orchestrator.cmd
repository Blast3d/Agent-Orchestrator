@echo off
setlocal
call "%~dp0Run Python.cmd" "%~dp0app\portable_setup.py" --quiet
if errorlevel 1 exit /b %errorlevel%
call "%~dp0Run Python.cmd" "%~dp0orchestrator.py" %*
exit /b %errorlevel%
