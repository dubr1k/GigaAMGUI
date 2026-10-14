# GigaAM Transcriber 2.6.4

Патч-релиз исправляет блокеры выпуска Liquid 2.6.3.

## Исправлено

- **Результаты больше не перезаписывают существующие файлы.** При совпадении имени новый экспорт получает следующий свободный суффикс: `meeting_1.txt`, `meeting_2.txt` и так далее.
- **Intel-сборка macOS снова собирается.** Сборочный workflow больше не зависит от удалённого Evermeet download API; x86_64 `ffmpeg` и `ffprobe` берутся из versioned GitHub Release.
- Проверка коллизий имён запускается в CI до тегового релиза.

---

## English

Patch release fixing the Liquid 2.6.3 release blockers.

### Fixed

- **Exports no longer overwrite existing results.** When a name conflicts, the new export gets the next free suffix: `meeting_1.txt`, `meeting_2.txt`, and so on.
- **The Intel macOS bundle builds again.** The workflow no longer relies on the retired Evermeet download API; x86_64 `ffmpeg` and `ffprobe` come from a versioned GitHub Release.
- Output-name collision coverage now runs in CI before a tagged release.
