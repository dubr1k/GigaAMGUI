@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

echo ============================================================
echo  GigaAM Transcriber — Сборка EXE
echo ============================================================
echo.

:: ── Найти активный Python ────────────────────────────────────────────────
set PYTHON=
for %%P in (python python3) do (
    if not defined PYTHON (
        %%P --version >nul 2>&1 && set PYTHON=%%P
    )
)

if not defined PYTHON (
    echo [ERROR] Python не найден в PATH.
    echo Активируйте виртуальное окружение: venv\Scripts\activate
    pause
    exit /b 1
)

%PYTHON% --version
echo.

:: ── Проверить gigaam ─────────────────────────────────────────────────────
%PYTHON% -c "import gigaam" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Пакет gigaam не найден.
    echo Установите зависимости: pip install -r requirements.txt
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
if exist "dist\GigaAMTranscriber" rmdir /s /q "dist\GigaAMTranscriber"
if exist "build\GigaAMTranscriber" rmdir /s /q "build\GigaAMTranscriber"

:: ── Сборка ───────────────────────────────────────────────────────────────
echo.
echo [3/3] Сборка EXE (может занять 5-15 минут)...
echo.
%PYTHON% -m PyInstaller gigaam_app.spec --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] Сборка завершилась с ошибкой.
    echo Проверьте вывод выше на наличие ошибок.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  СБОРКА УСПЕШНА!
echo  EXE находится в: dist\GigaAMTranscriber\GigaAMTranscriber.exe
echo.
echo  ВАЖНО: При первом запуске приложение автоматически скачает
echo  модель GigaAM (~1-2 GB) в C:\HuggingFaceCache
echo  Интернет-соединение обязательно при первом запуске.
echo ============================================================
echo.
pause
