@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

rem cd в корень проекта (скрипт лежит в scripts\)
cd /d "%~dp0.."

echo ============================================================
echo  GigaAM Transcriber - ПОРТАТИВНАЯ onefile сборка
echo ============================================================
echo.
echo  Результат: dist\GigaAMTranscriber_portable.exe (ОДИН файл)
echo  PyTorch НЕ пакуется внутрь - скачивается при первом запуске
echo  в C:\GigaAMGUICash (CPU / GPU 20-40xx / GPU 50xx на выбор).
echo.

:: ── Найти активный Python ────────────────────────────────────────────────
set PYTHON=
for %%P in (python python3) do (
    if not defined PYTHON (
        %%P --version >nul 2>&1 && set PYTHON=%%P
    )
)
if not defined PYTHON (
    echo [ERROR] Python не найден в PATH. Активируйте окружение.
    pause
    exit /b 1
)
%PYTHON% --version
echo.

:: ── Проверить gigaam ─────────────────────────────────────────────────────
%PYTHON% -c "import gigaam" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Пакет gigaam не найден. pip install -r requirements.txt
    pause
    exit /b 1
)
echo [OK] gigaam найден

:: ── Установить / обновить PyInstaller ────────────────────────────────────
echo.
echo [1/3] Установка PyInstaller...
%PYTHON% -m pip install pyinstaller --upgrade -q
if errorlevel 1 (
    echo [ERROR] Не удалось установить PyInstaller
    pause
    exit /b 1
)
echo [OK] PyInstaller готов

:: ── Очистить старую сборку ───────────────────────────────────────────────
echo.
echo [2/3] Очистка предыдущей сборки...
if exist "dist\GigaAMTranscriber_portable.exe" del /q "dist\GigaAMTranscriber_portable.exe"
if exist "build\GigaAMTranscriber_portable" rmdir /s /q "build\GigaAMTranscriber_portable"

:: ── Сборка ───────────────────────────────────────────────────────────────
echo.
echo [3/3] Сборка onefile EXE (может занять 5-15 минут)...
echo.
%PYTHON% -m PyInstaller packaging\gigaam_app_portable.spec --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] Сборка завершилась с ошибкой.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  СБОРКА УСПЕШНА!
echo  Файл: dist\GigaAMTranscriber_portable.exe
echo.
echo  При ПЕРВОМ запуске приложение спросит устройство
echo  (CPU / GPU 20-40xx / GPU 50xx) и скачает нужный PyTorch
echo  в C:\GigaAMGUICash. Нужен интернет при первой настройке.
echo  Сменить устройство позже: меню Настройки -^> Устройство.
echo ============================================================
echo.
pause
