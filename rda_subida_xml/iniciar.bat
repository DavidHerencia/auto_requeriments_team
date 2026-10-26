@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv || exit /b 1
  .venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
  .venv\Scripts\python.exe -m playwright install chrome || exit /b 1
)
.venv\Scripts\python.exe app.py --help
pause
