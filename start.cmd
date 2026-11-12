@echo off
rem Double-click to start (or check) the Prompt Workbench, then open it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if %ERRORLEVEL% EQU 0 start "" http://127.0.0.1:8789
pause
