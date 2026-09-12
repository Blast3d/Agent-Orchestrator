@echo off
setlocal
title Gemini via Antigravity
cd /d "%~dp0..\runtime\workspaces\google"
set "GEMINI_API_KEY="
set "GOOGLE_API_KEY="
set "GOOGLE_GENAI_USE_VERTEXAI="
set "TERM=xterm-256color"
set "COLORTERM=truecolor"
echo Gemini through Antigravity. AI-credit overage is disabled.
"%LOCALAPPDATA%\agy\bin\agy.exe" --model gemini-3.8-flash-medium --mode plan
pause
