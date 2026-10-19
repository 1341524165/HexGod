@echo off
setlocal
set "HEXGOD_PROJECT_DIR=%~dp0"
if not exist "%HEXGOD_PROJECT_DIR%runtime\python.exe" (
    echo Missing runtime\python.exe. Please keep the full hexgod folder together.
    pause
    exit /b 1
)
"%HEXGOD_PROJECT_DIR%runtime\python.exe" -X utf8 -B "%HEXGOD_PROJECT_DIR%scripts\bootstrap_runtime.py"
if errorlevel 1 (
    pause
    exit /b 1
)
if not exist "%HEXGOD_PROJECT_DIR%build\bin\hexgod.exe" (
    echo Desktop build not found. Run scripts\build.ps1 first.
    pause
    exit /b 1
)
start "" "%HEXGOD_PROJECT_DIR%build\bin\hexgod.exe"
exit /b 0
