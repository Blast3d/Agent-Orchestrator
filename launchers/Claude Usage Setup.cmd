@echo off
setlocal
title Claude - Usage monitor setup
cd /d "%~dp0..\runtime\workspaces\claude"
set "ANTHROPIC_API_KEY="
set "TERM=xterm-256color"
set "COLORTERM=truecolor"
echo Complete Claude's normal first-run setup using your existing Claude subscription.
echo If it returns a sign-in code, paste it into THIS window, not into chat.
echo Once the normal prompt appears, tell Codex that setup is complete.
call claude.cmd --safe-mode --tools ""
pause
