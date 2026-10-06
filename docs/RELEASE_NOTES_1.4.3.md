# GigaAM Transcriber 1.4.3

## Русский

### Изменено

- Очередь файлов больше не «застревает» между запусками. Раньше приложение
  сохраняло сами пути к выбранным аудиофайлам и транскриптам для LLM и после
  перезапуска подставляло тот же список — включая файлы, которые уже были
  обработаны в прошлой сессии.
- Теперь при старте приложение помнит только папку последнего выбора и
  пересканирует её заново:
  - для аудио — пропускает файлы, для которых в папке результатов уже есть
    транскрипт хотя бы в одном из включённых форматов вывода;
  - для транскриптов LLM-обработки — пропускает файлы, для которых уже есть
    результат по всем включённым действиям (резюме/задачи/своё) и форматам
    экспорта, а также не учитывает как входные сами `..._llm_*` результаты.
- Остальные настройки (форматы вывода, диаризация, LLM-конфигурация, геометрия
  окна, активная вкладка) не затронуты и продолжают сохраняться между
  запусками как раньше.

## English

### Changed

- The file queue no longer "sticks" across restarts. The app used to persist
  the exact paths of previously selected audio files and LLM transcripts and
  repopulate that same list on the next launch — including files already
  processed in the previous session.
- On startup the app now remembers only the last-used folder and rescans it:
  - for audio, files that already have a transcript in at least one enabled
    output format are skipped;
  - for LLM transcripts, files that already have a result for every enabled
    action (summary/tasks/custom) and export format are skipped, and the
    LLM's own `..._llm_*` output files are excluded from the input scan.
- Everything else (output formats, diarization, LLM configuration, window
  geometry, active tab) is unaffected and still persists across restarts as
  before.
