@echo off
setlocal
cd /d "%~dp0"

if exist "dist\XrayWifiShare\XrayWifiShare.exe" (
  echo Запуск XrayWifiShare.exe...
  start "" "dist\XrayWifiShare\XrayWifiShare.exe"
  exit /b 0
)

where python >nul 2>&1
if errorlevel 1 (
  echo Python не найден. Сначала собери exe: build_exe.bat
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Создаю venv и ставлю зависимости...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call ".venv\Scripts\activate.bat"
)

echo Запуск из Python (exe ещё не собран — build_exe.bat)...
powershell -NoProfile -Command "Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList 'main.py' -Verb RunAs -WorkingDirectory '%cd%'"
endlocal
