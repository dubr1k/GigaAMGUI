# Рефакторинг монолитов: core → services → adapters

**Дата:** 2026-07-11
**Статус:** утверждён дизайн, готов к плану реализации
**Подход:** C (полная реархитектура), выполняемая инкрементально под характеризующими тестами
**Инвариант:** поведение всех четырёх поверхностей (GUI / CLI / API / Web) — строго 1:1

---

## 1. Проблема

Проект имеет четыре поверхности вокруг GigaAM-v3: Desktop GUI (PyQt6), CLI, REST API
(FastAPI), Web GUI. Ядро (`src/core/`, `src/utils/`) уже общее и импортируется всеми
поверхностями. Но **service-уровневая** логика написана инлайн внутри файлов-поверхностей
и потому продублирована и расходится.

### 1.1. Файлы-монолиты

| Файл | Строк | Что намешано |
|---|---|---|
| `src/gui/app_qt.py` | 3834 | UI-вёрстка + темы/i18n + настройки + загрузка медиа + весь LLM-диспетчер + ASR-выбор в одном классе `GigaTranscriberQtApp` |
| `web/web_app.py` | 1484 | FastAPI-роуты + task-registry + persistence + auth + ASR-health + свой LLM-диспетчер |
| `api.py` | 1456 | FastAPI-роуты + task-registry + persistence + auth + ASR-health |
| `src/core/processor.py` | 634 | транскрибация + диаризация + генераторы SRT/VTT/MD |
| `cli.py` | 677 | CLI + прогресс-рендер + интерактивный выбор файлов |

### 1.2. Дублирование (первичная проблема — важнее размера)

- **LLM-диспетчер**: `_run_llm_provider`, `_run_api_llm`, `_run_claude_code_llm`,
  `_run_codex_llm`, `_run_opencode_llm`, `_run_pi_llm`, `_run_other_llm`,
  `_build_llm_prompt_text` — продублированы в `app_qt.py` **и** `web_app.py`.
  При этом уже существует `src/utils/llm_client.py` (`LLMClient`/`LLMSettings`),
  который web использует лишь частично, а GUI обходит полностью.
- **Task-registry / persistence**: `tasks_storage`, `_register_task`,
  `restore_tasks_from_results`, `_restore_completed_task_from_meta`,
  index/tombstone-persistence — продублированы в `api.py` **и** `web_app.py`
  (почти идентичная схема; web добавляет `user`, `output_formats`,
  `enable_diarization`, `num_speakers`).
- **Health/runtime**: `_asr_health`, `_runtime_info` — в `api.py` **и** `web_app.py`.
- **Валидация файлов**: `is_supported_format`, `safe_filename` — в `api.py` **и** `web_app.py`.
- **Обвязка транскрибации**: `ModelLoader` + `TranscriptionProcessor` + `ProcessingStats`
  собираются вручную в четырёх местах (`api.py`, `web_app.py`, `cli.py`, `app_qt.py`).

---

## 2. Целевая архитектура

Четыре слоя, зависимости строго вниз:

```
Adapters (тонкие)   src/gui/  cli/  api/  web/
      │  только: парсинг ввода, вызов сервиса, рендер вывода
      ▼
Services (НОВЫЙ)    src/services/
      │  llm_service · task_store · health · file_policy · transcription_service
      ▼
Core                src/core/   (существует: ModelLoader, TranscriptionProcessor, asr/)
      ▼
Utils / Infra       src/utils/  (существует: audio, media, llm_client, json, naming…)
```

### 2.1. Новый слой `src/services/`

| Модуль | Поглощает | Ответственность |
|---|---|---|
| `llm_service.py` | `_run_llm_provider` + `_run_*_llm` + `_build_llm_prompt_text` из `app_qt.py` и `web_app.py` | единый диспетчер провайдеров: API-путь через существующий `LLMClient`; CLI-провайдеры (Claude Code, Codex, OpenCode, Pi, Other) через общий subprocess-раннер; построение prompt |
| `task_store.py` | `tasks_storage`, `_register_task`, restore/persist/tombstone из `api.py` и `web_app.py` | единая модель Task + жизненный цикл + persistence; web-специфичные поля как расширение схемы, а не отдельная копия |
| `health.py` | `_asr_health`, `_runtime_info` из `api.py` и `web_app.py` | единый источник статуса ASR и runtime-инфо |
| `file_policy.py` | `is_supported_format`, `safe_filename` из `api.py` и `web_app.py` | валидация формата и безопасного имени файла |
| `transcription_service.py` | ручная обвязка ModelLoader+Processor+stats из 4 поверхностей | единая точка запуска транскрибации файла с прогресс-колбэком |

Web-специфика LLM/task (auth-пользователь, форматы, диаризация) моделируется как
расширение общих структур (доп. поля / подкласс / параметры), а не как параллельная копия.

### 2.2. Декомпозиция монолитов

- **`app_qt.py` → пакет `src/gui/`:**
  `main_window.py` (сборка окна) · `theme.py` (темы, `_apply_theme`, шрифты, `_px/_pt`) ·
  `i18n.py` (`_t`, `_apply_language`, translator) · `llm_tab.py` (LLM-вкладка → `llm_service`) ·
  `settings_panel.py` (restore/save UI-настроек) · `download_controller.py` (загрузка медиа) ·
  `files_panel.py` (список файлов, drag-drop). Бог-класс распадается на виджеты/контроллеры.
- **`api.py` → пакет `api/`:** `app.py` (сборка FastAPI) + `routers/` (tasks, upload, keys,
  health) + `deps.py` (auth). Роуты тонкие поверх сервисов.
- **`web_app.py` → пакет `web/`:** `app.py` + `routers/` (auth, tasks, upload, llm) поверх
  тех же сервисов.
- **`processor.py` → ядро + `src/core/formatters/`:** вынести `_generate_srt`, `_generate_vtt`,
  `_generate_markdown` (и `web_app._detect_format`) в форматтеры; processor их вызывает.
- **`cli.py` → пакет `cli/`:** `main.py` + `interactive.py` (выбор файлов) + `render.py`
  (прогресс, вывод результатов).

### 2.3. Не трогаем

`src/core/asr/*`, `src/utils/*` (кроме форматтеров, вынесенных из processor), `config.py`,
`model_loader.py` — границы уже приемлемые.

---

## 3. Стратегия миграции

Принцип каждой фазы: **характеризующие тесты → extract → переключение поверхностей →
удаление старого кода.** Каждая фаза заканчивается зелёными тестами и четырьмя рабочими
поверхностями. Порядок — от низкого риска/высокого ROI к высокому риску.

### Фаза 0 — Гигиена репозитория (предусловие)
Убрать из рабочего дерева артефакты, искажающие анализ и мешающие тестам:
`*.sync-conflict-*`, `.deploy-backups/`, `.opencode-backups/`, `.omo/`, `.mimocode/`,
`.hermes/` → `.gitignore` / удаление. Зафиксировать зелёный baseline (`ruff` + `pytest`).

### Фаза 1 — Общий сервис-слой без дублей
Порядок по возрастанию сложности; после каждого пункта старые функции в монолитах —
тонкие обёртки-делегаты над сервисом, тесты зелёные:
1. `file_policy.py` ← `is_supported_format` / `safe_filename` (чистые функции).
2. `health.py` ← `_asr_health` / `_runtime_info`.
3. `llm_service.py` ← диспетчер провайдеров (API → `LLMClient`, CLI-провайдеры общие).
4. `task_store.py` ← модель Task + register/persist/restore (web-поля как расширение).
5. `transcription_service.py` ← общая обвязка ModelLoader+Processor+stats.

### Фаза 2 — Форматтеры ядра
Вынести `_generate_srt/_vtt/_markdown` из `processor.py` в `src/core/formatters/`;
перенести туда же `web_app._detect_format`. Processor и web вызывают форматтеры.

### Фаза 3 — Роутеризация backend
`api.py` → пакет `api/` (app + routers + deps), `web_app.py` → пакет `web/`. Роуты — тонкие
поверх сервисов Фазы 1. Точки входа (`app.py`, `docker-compose.yml`, `Dockerfile`,
PyInstaller `.spec`) остаются рабочими.

### Фаза 4 — Разбор GUI-монолита (самый рискованный, идёт поздно)
`app_qt.py` → пакет `src/gui/` (theme / i18n / llm_tab / settings_panel /
download_controller / files_panel / main_window), под GUI-характеризующими тестами.

### Фаза 5 — CLI + финальная чистка
`cli.py` → пакет `cli/`; удалить оставшиеся обёртки-делегаты; финальная сверка точек входа
и всех сборочных spec-файлов.

---

## 4. Тестирование и верификация (гарантия 1:1)

- **Baseline:** существующие `tests/` (processor/cli/api progress, web persistence, backends,
  media downloader, audio converter, …) должны оставаться зелёными на каждом шаге.
- **Характеризующие тесты перед каждым extract:** где сервис-логику ещё не покрывает тест,
  сначала пишем тест на *текущее* поведение, затем выносим — тест ловит регресс 1:1.
- **Паритет-тест дубля:** для LLM/task_store/health — тест, что старый путь и новый сервис
  дают идентичный результат, до удаления старого кода.
- **GUI:** headless-тесты логики контроллеров (`QT_QPA_PLATFORM=offscreen`): сборка LLM-настроек,
  диспетч провайдера, merge путей, toggle форматов. Вёрстку построчно не тестируем, но
  проверяем, что окно конструируется без ошибок.
- **Гейты фазы:** `ruff` + весь `pytest` зелёные + smoke каждой изменённой поверхности
  (импорт / `--help` / health-эндпоинт) перед переходом к следующей фазе.
- **Метод:** реализация каждой фазы через TDD; завершение фазы — через
  verification-before-completion (запуск команд, наблюдение вывода, только потом «готово»).

---

## 5. Явные не-цели (YAGNI)

- Не меняем пользовательское поведение, форматы вывода, тексты, API-контракты.
- Не переписываем ASR-backends, `config.py`, `model_loader.py`.
- Не вводим новые фреймворки/зависимости ради «чистоты».
- Не делаем несвязанный рефакторинг за пределами перечисленных монолитов и дублей.
