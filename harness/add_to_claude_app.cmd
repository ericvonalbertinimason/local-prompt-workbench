@echo off
rem Quit Claude first (system tray icon > Quit), then double-click this file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0add_to_claude_app.ps1"
pause
