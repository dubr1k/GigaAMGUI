# GigaAM Transcriber 2.6.7

## Исправлено

- **Подготовка runtime для компонентов на базе `torch` (#72).** Перед пакетной обработкой приложение подготавливает и активирует app-managed runtime, когда выбранные компоненты требуют `torch`. Отмена установки не запускает обработку; режим только ONNX не инициирует загрузку runtime.
- **Совместимость сборки macOS Intel.** Для транзитивных зависимостей PyObjC сохранён известный совместимый набор 12.2.2.
- **Совместимость замороженной сборки.** PyQt6, PyQt6-Qt6 и PyQt6-sip закреплены на 6.11.0, 6.11.2 и 13.13.0. В CI зависимости сборки PyInstaller и pyinstaller-hooks-contrib закреплены на 6.22.3 и 2026.8.

Тег 2.6.6 не опубликован: сборка остановилась на macOS Qt selfcheck при работе PyInstaller с `QtQuickControls2Macos.framework/QtQuickControls2Macos`.

## English

# GigaAM Transcriber 2.6.7

## Fixed

- **Runtime preparation for `torch`-based components (#72).** Before batch processing, the app prepares and activates its app-managed runtime when selected components require `torch`. Cancelling installation does not start processing; ONNX-only use does not initiate a runtime download.
- **Intel macOS build compatibility.** The previously compatible PyObjC 12.2.2 dependency set is retained.
- **Frozen-build compatibility.** PyQt6, PyQt6-Qt6, and PyQt6-sip are pinned to 6.11.0, 6.11.2, and 13.13.0. CI build dependencies PyInstaller and pyinstaller-hooks-contrib are pinned to 6.22.3 and 2026.8.

The 2.6.6 tag was not published: its build stopped at the macOS Qt selfcheck while PyInstaller handled `QtQuickControls2Macos.framework/QtQuickControls2Macos`.
