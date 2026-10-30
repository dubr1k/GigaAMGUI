# GigaAM Transcriber 2.6.6

## Исправлено

- **Подготовка runtime для компонентов на базе `torch` (#72).** До запуска пакетной обработки приложение теперь обязательно подготавливает и активирует app-managed runtime, если выбранные компоненты требуют `torch`. Отмена установки не запускает обработку и оставляет GUI доступным. При использовании только ONNX загрузка runtime не начинается; существующая native-среда не заменяется.
- **Сборка macOS Intel.** Транзитивные зависимости PyObjC закреплены на известном ранее рабочем наборе версии 12.2.2. Набор 13.0 приводил к ошибке импорта CoreMedia из-за отсутствующей `libswiftCoreMedia.dylib`; traceback импорта теперь доступен в диагностике вместо скрытого.

## English

# GigaAM Transcriber 2.6.6

## Fixed

- **Runtime preparation for `torch`-based components (#72).** Before batch processing starts, the app now prepares and activates its app-managed runtime whenever selected components require `torch`. Cancelling installation does not start processing and leaves the GUI usable. ONNX-only use does not initiate a runtime download, and an existing native environment is preserved.
- **Intel macOS build compatibility.** Transitive PyObjC dependencies are pinned to the previously known-good 12.2.2 set. Version 13.0 caused CoreMedia import failure because `libswiftCoreMedia.dylib` was missing; import tracebacks are now visible in diagnostics rather than hidden.

The 2.6.5 tag build was not published after an Intel build failure. These notes do not claim a reporter-confirmed resolution or a passing Windows CI run.
