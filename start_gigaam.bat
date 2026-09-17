@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo GigaAM: не найдено виртуальное окружение venv.
    echo Выполните установку зависимостей из docs\INSTALL_WINDOWS.md.
    pause
    exit /b 1
)

"venv\Scripts\python.exe" app.py
pause
