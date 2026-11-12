@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call ".venv\Scripts\activate.bat"
)

pip install -q pyinstaller

echo Building exe...
pyinstaller --noconfirm --clean xray-wifi-share.spec
if errorlevel 1 (
  echo Build failed
  exit /b 1
)

if not exist "runtime\bin\xray.exe" (
  echo Downloading runtime binaries...
  python -c "from app.deps import ensure_runtime; ensure_runtime(print)"
)

echo Copying runtime binaries next to exe...
mkdir "dist\XrayWifiShare\runtime\bin" 2>nul
mkdir "dist\XrayWifiShare\runtime\work" 2>nul
xcopy /Y /I /E "runtime\bin\*" "dist\XrayWifiShare\runtime\bin\" >nul

echo.
echo Ready: dist\XrayWifiShare\XrayWifiShare.exe
echo Run as admin (UAC prompt on start).
endlocal
