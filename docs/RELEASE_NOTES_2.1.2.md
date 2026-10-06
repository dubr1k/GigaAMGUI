# GigaAM Transcriber 2.1.2

Технический релиз без изменений в приложениях: тот же самостоятельный
GigaAMLiquid и те же PyQt/CLI/API, что в 2.1.1. Выпущен, чтобы опубликованные
архивы совпадали с `main`, где укреплена проверка companion в CI.

## Что изменилось

- Проверка замороженного Python-рантайма в CI больше не зависит от `say` на
  раннере. Клип для батч- и live-smoke теперь фикстура
  `tests/fixtures/liquid_smoke.wav` (4 с речи, 16 кГц, 130 КБ).
- Батч-smoke падает, если распознанный текст пуст: раньше молчаливый клип
  считался успехом и ничего не доказывал.

## Не менялось

- Распознавание, диаризация, Live и LLM в GigaAMLiquid и PyQt работают как в
  2.1.1. Пустые результаты распознавания в самих приложениях обрабатываются
  по-прежнему: пустой файл даёт пустой транскрипт с предупреждением в журнале.

## Packaging

- Версии PyQt, AppKit, Tauri, npm и Cargo синхронизированы на `2.1.2`.

---

Maintenance release: no application changes. The CI companion smoke now uses a
committed 4-second clip instead of the runner's `say` and fails on an empty
transcript; v2.1.1 needed a manual job rerun after `say` produced a silent clip.
