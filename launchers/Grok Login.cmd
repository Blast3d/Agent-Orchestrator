@echo off
setlocal
title Grok - SuperGrok account sign-in
cd /d "%~dp0..\runtime\workspaces\grok"
set "XAI_API_KEY="
set "GROK_API_KEY="
set "TERM=xterm-256color"
set "COLORTERM=truecolor"
echo Sign in to your SuperGrok account in the browser when prompted.
echo Return here after completing the account authorization.
"%USERPROFILE%\.grok\bin\grok.exe" --no-auto-update login --oauth
if errorlevel 1 goto failed
echo Login command completed. Codex will verify a model task next.
echo completed>"%~dp0..\runtime\grok-login-state.txt"
pause
exit /b 0
:failed
echo Login did not complete. Keep this window open for troubleshooting.
pause
exit /b 1
