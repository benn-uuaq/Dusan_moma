@echo off
rem TPAC 브릿지(단독판) exe 만들기 — 이 폴더(src)에서 실행한다.
rem 필요: Python 3 + pip install pyModbusTCP pyinstaller
chcp 65001 >nul
setlocal
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --windowed --name TPAC_Bridge ^
  --distpath "%~dp0.." --workpath "%TEMP%\tpac_bridge_build" --specpath "%TEMP%\tpac_bridge_build" ^
  tpac_bridge_app.py
if errorlevel 1 (
  echo 빌드 실패
  exit /b 1
)
echo 만들었습니다: %~dp0..\TPAC_Bridge.exe
endlocal
