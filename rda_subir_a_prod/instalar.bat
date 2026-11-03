@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Instala Python o habilita el comando py
  pause
  exit /b 1
)
py -3 -m venv .venv
if errorlevel 1 (
  echo [ERROR] No se pudo crear entorno virtual
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Dependencias no instaladas
  pause
  exit /b 1
)
echo Listo. Ejecuta iniciar.bat
pause
