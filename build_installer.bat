@echo off
setlocal
cd /d "%~dp0"

call build_exe.bat
if errorlevel 1 exit /b 1

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if "%ISCC%"=="" (
  echo Inno Setup 6 не найден. Установи: winget install JRSoftware.InnoSetup
  exit /b 1
)

mkdir "dist\installer" 2>nul
"%ISCC%" "installer\xray-wifi-share.iss"
if errorlevel 1 exit /b 1

echo.
echo Installer: dist\installer\XrayWifiShare-Setup-1.1.0.exe
endlocal
