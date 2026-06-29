@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

"%PYTHON%" -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 exit /b 1

"%PYTHON%" -m PyInstaller --clean --noconfirm msu2-linux.spec
if errorlevel 1 exit /b 1

echo EXE build completed: dist\msu2-linux.exe
endlocal
