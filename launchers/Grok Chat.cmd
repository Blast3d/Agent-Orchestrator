@echo off
setlocal
title Grok - SuperGrok account
cd /d "%~dp0..\runtime\workspaces\grok"
set "XAI_API_KEY="
set "GROK_API_KEY="
set "TERM=xterm-256color"
set "COLORTERM=truecolor"
"%USERPROFILE%\.grok\bin\grok.exe" --no-auto-update --model grok-4.6
pause
