@echo off
rem Prompt Workbench tray app: Ctrl+Alt+P improves the selected text (or the clipboard).
rem First run: creates its private Python environment (pystray + Pillow only).
set ROOT=%~dp0
if not exist "%ROOT%.venv\Scripts\pythonw.exe" (
  py -3 -m venv "%ROOT%.venv" || python -m venv "%ROOT%.venv"
  "%ROOT%.venv\Scripts\python.exe" -m pip install --quiet pystray==0.19.5 pillow
)
start "" "%ROOT%.venv\Scripts\pythonw.exe" "%ROOT%workbench_tray.py"
