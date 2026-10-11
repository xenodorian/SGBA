@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" runtime.py games/demo/main.py %*
) else (
  python runtime.py games/demo/main.py %*
)
