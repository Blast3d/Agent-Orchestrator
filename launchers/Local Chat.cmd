@echo off
setlocal
title Local AI - Light Chat
call "%~dp0..\Run Python.cmd" "%~dp0..\app\portable_setup.py" --quiet
if errorlevel 1 exit /b %errorlevel%
call "%~dp0..\Run Python.cmd" "%~dp0..\app\manage_local.py" chat
if errorlevel 1 pause
