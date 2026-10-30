# GigaAM Transcriber 2.6.8

## Исправлено

- **Запуск собранного приложения (#74).** PyInstaller теперь включает модули из карт ленивых экспортов `src.utils` и `src.core`, в том числе `src.utils.logger`. Исправление применяется ко всем вариантам сборки; ленивый порядок загрузки ML-компонентов сохранён.
- **Проверка импортов GUI перед публикацией.** Релизные бинарники Windows, macOS и Linux, а также macOS `.app` проходят импорт настоящей цепочки зависимостей GUI без скачивания моделей и установки runtime. Отсутствующая зависимость останавливает публикацию релиза.

## English

### Fixed

- **Frozen application startup (#74).** PyInstaller now includes the modules declared by the lazy export maps in `src.utils` and `src.core`, including `src.utils.logger`. The fix applies to all build variants while preserving lazy ML initialization.
- **GUI import gate before publication.** Release binaries on Windows, macOS and Linux, as well as macOS `.app` bundles, import the real GUI dependency chain without downloading models or installing a runtime. A missing dependency blocks release publication.
