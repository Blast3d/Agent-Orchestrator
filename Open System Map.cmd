@echo off
call "%~dp0Run Python.cmd" "%~dp0app\portable_setup.py" --quiet
if errorlevel 1 exit /b %errorlevel%
if not "%~1"=="" (
  call "%~dp0Run Python.cmd" "%~dp0orchestrator.py" map %*
) else (
  call "%~dp0Run Python.cmd" "%~dp0orchestrator.py" viewer --page system-map
)
if errorlevel 1 pause
