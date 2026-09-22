# GigaAM Transcriber 2.5.0

Большой релиз: переработанный терминальный клиент (TUI 2.0), новый REST API,
совместимый с OpenAI Audio API, и MCP-сервер для ИИ-агентов. Старое REST API
`/api/v1/*` удалено — это несовместимое изменение для тех, кто ходил к серверу
напрямую; настольные приложения, веб-панель и TUI не затронуты.

## Добавлено

### TUI 2.0

- **Русский интерфейс по умолчанию.** `/lang ru|en` (или `--lang` на один
  запуск) переключает язык; значение хранится в общей с настольным приложением
  настройке `language`. Переведены все статусы, подсказки, справка и описания
  команд; headless-вывод для агентов (`gigaam transcribe`, `gigaam llm`)
  остаётся английским.
- **Вкладки Обработка / LLM / Настройки / Журнал** — F1–F4, Tab/Shift+Tab или
  клик. На «Обработке» рядом с очередью — панель параметров (движок, модель,
  форматы, диаризация, спикеры, звук, папка): Enter или клик меняет значение.
  Строка «▶ Далее:» подсказывает следующий шаг, `?` открывает справку со всеми
  клавишами и командами.
- **Вкладка «Настройки» вместо меню `/settings`**: все параметры одним
  списком, включая LLM-провайдера, ключ (скрыт), пета, мышь и язык.
- **LLM-страница**: список транскриптов, режимы-галочки (Space), провайдер,
  прокручиваемый ответ; при нескольких режимах показываются все ответы с
  заголовками.
- **Мышь**: вкладки, кнопки, строки очереди и настроек, колёсико прокрутки.
  `/mouse off` возвращает выделение текста терминалу.
- Обновление до ratatui 0.30 / crossterm 0.29 / ratatui-image 11; `main.rs`
  разбит на модули.

### REST API по образцу OpenAI

- `POST /v1/audio/transcriptions` — multipart `file` + `model` (`whisper-1`,
  `gpt-4o-transcribe` и другие алиасы указывают на модель по умолчанию;
  `v3_e2e_rnnt`, `multilingual_ctc`, `multilingual_large_ctc` — явно).
  `response_format`: `json` (по умолчанию), `text`, `srt`, `vtt`,
  `verbose_json` (сегменты, по запросу — слова), `diarized_json` (реплики с
  буквами говорящих A, B…). `stream=true` — SSE с событиями
  `transcript.text.delta` / `transcript.text.done`; пока идёт распознавание,
  сервер шлёт комментарии `: progress …`, чтобы соединение не рвалось.
- Расширения GigaAM теми же полями формы: `diarize`, `diarization_backend`,
  `num_speakers`, `asr_backend`, `onnx_provider`, `audio_preprocessing`.
- `GET /v1/models`, `GET /v1/models/{id}` — список моделей в формате OpenAI с
  блоком `gigaam` (бэкенды, ONNX-провайдеры, активная конфигурация).
- Авторизация `Authorization: Bearer <ключ>` (как в SDK OpenAI); `X-API-Key`
  принимается для совместимости. Хранилище ключей `.api_keys` прежнее.
- Ошибки — в конверте OpenAI `{"error": {"message", "type", "param", "code"}}`
  для всех статусов, 429 — с `Retry-After`.
- Работает с официальными SDK: достаточно `base_url="http://127.0.0.1:8000/v1"`
  и ключа. Справочник — `docs/API.md`, Postman-коллекция обновлена.

### MCP-сервер для ИИ-агентов

- Claude Code, Codex, Cursor и любой MCP-клиент получают GigaAM как набор
  инструментов: `transcribe` (аудио/видео по `url`, пути на сервере или
  `audio_base64` → текст, сегменты, говорящие, SRT/VTT; во время работы —
  progress notifications со стадией и процентом), `summarize` (выжимка,
  задачи, термины или свой промпт через настроенный LLM-провайдер),
  `list_models`, `list_llm_providers`, `server_status`; ресурсы
  `gigaam://models`, `gigaam://status`; промпты `meeting_notes` (протокол
  встречи) и `subtitles_review` (проверка SRT).
- Локально: `gigaam mcp` (stdio, входит в установку TUI) —
  `claude mcp add gigaam -- gigaam mcp`; файлы с этой же машины передаются по
  пути. Удалённо: `/mcp` в `api.py` и в веб-панели (в Docker-образе тоже) —
  `claude mcp add --transport http gigaam https://gigaam-site.dubr1k.space/mcp
  --header "Authorization: Bearer <key>"`; ключ тот же, что у REST API, без
  него — 401. В HTTP-режиме пути на сервере запрещены, пока не задан
  `GIGAAM_MCP_ALLOW_PATHS=1` (`GIGAAM_MCP_PATH_ROOT` ограничивает каталог);
  лимит base64 — `GIGAAM_MCP_MAX_INLINE_MB` (25 МБ).
- Скиллы для агентов: `skills/gigaam` теперь рассказывает про MCP, новый
  `skills/gigaam-mcp` — самостоятельный скилл для агентов без локальной
  установки (контракт инструментов, выбор источника, ошибки, рецепты);
  `gigaam --install-skill` ставит оба. Справочник — `docs/MCP.md`, фрагмент
  nginx для SSE-потока — `deploy/nginx-mcp-location.conf`.

## Изменено (breaking)

- Удалены `/api/v1/transcribe`, `/api/v1/transcribe/batch`, `/api/v1/tasks*`,
  `/api/v1/download-batch`, `/api/v1/asr/options`, очередь задач и хранение
  результатов на диске. Ответ теперь возвращается синхронно в том же запросе.
  Примеры в настольных приложениях и документации переведены на новый API;
  старые руководства по API и Postman удалены в пользу `docs/API.md`.
- Переменные `TASK_CLEANUP_HOURS` и `RATE_LIMIT_API` больше не читаются;
  `RATE_LIMIT_UPLOAD` теперь действительно ограничивает частоту запросов на
  транскрибацию.

## Исправлено

- **Воркер TUI завершался сразу после старта («Worker exited»).** Стартовая
  проверка CLI-провайдеров передавала детям stdin воркера, и `pi --version`
  выставлял на нём O_NONBLOCK — воркер принимал EAGAIN за EOF и выходил.
  Пробы и LLM-запуски без ввода получают `DEVNULL`, а свой stdin воркер
  читает устойчиво к смене флагов.
- Первый Esc во время обработки посылает воркеру мягкую отмену, как обещает
  подсказка; второй — перезапуск.
- В API загрузка проверяется до чтения тела: без заголовка авторизации или с
  `Content-Length` больше лимита сервер отвечает сразу, не сохраняя файл.

## Что проверить

- `gigaam --update` → `gigaam --version` печатает `gigaam-tui v2.5.0`;
  `gigaam` открывается по-русски, `/lang en` переключает язык и это же
  значение видно в настольном приложении.
- Клики мышью по вкладкам и строкам параметров; `?` — справка; `/mouse off`.
- `python api.py`, затем `curl http://127.0.0.1:8000/v1/audio/transcriptions
  -H "Authorization: Bearer $KEY" -F file=@a.mp3 -F model=whisper-1` — текст;
  `-F response_format=srt`, `-F stream=true`; `openai` SDK с
  `base_url=".../v1"`.
- Веб-панель (`web/`) и Docker-образ работают как раньше — они не используют
  `api.py`; в них появился `/mcp`, при первом старте веб-панель печатает ключ.
- `claude mcp add gigaam -- gigaam mcp`, затем в Claude Code `/mcp` показывает
  `gigaam`; попросите агента вызвать `server_status` и `transcribe` по пути к
  короткому файлу. Удалённо — `claude mcp add --transport http …` с ключом и
  `transcribe(url=…)` с короткого ролика.

---

## English

A major release: the reworked terminal client (TUI 2.0), a new REST API
compatible with the OpenAI Audio API, and an MCP server for AI agents. The old
`/api/v1/*` REST API is removed — a breaking change for direct API consumers;
the desktop apps, web panel and TUI are unaffected.

### Added

**TUI 2.0**

- Russian UI by default; `/lang ru|en` (or `--lang` for one run) switches the
  language and stores it in the `language` setting shared with the desktop app.
  Every status, hint, help entry and command description is bilingual; the
  headless output for agents (`gigaam transcribe`, `gigaam llm`) stays English.
- Tabs Processing / LLM / Settings / Log — F1–F4, Tab/Shift+Tab or a click. The
  Processing page shows a parameter panel next to the queue (engine, model,
  formats, diarization, speakers, audio, folder): Enter or a click changes a
  value. A “▶ Next:” line suggests the next step; `?` opens a help overlay with
  every key and command.
- A Settings tab replaces the `/settings` menu: all options in one list,
  including the LLM provider, the masked API key, the pet, mouse and language.
- LLM page: transcript list, mode checkboxes (Space), provider, scrollable
  answer; with several modes every answer is shown under its own heading.
- Mouse support: tabs, buttons, queue and settings rows, wheel scrolling.
  `/mouse off` hands text selection back to the terminal.
- ratatui 0.30 / crossterm 0.29 / ratatui-image 11; `main.rs` split into modules.

**OpenAI-compatible REST API**

- `POST /v1/audio/transcriptions` — multipart `file` + `model` (`whisper-1`,
  `gpt-4o-transcribe` and other aliases map to the default model;
  `v3_e2e_rnnt`, `multilingual_ctc`, `multilingual_large_ctc` select one
  explicitly). `response_format`: `json` (default), `text`, `srt`, `vtt`,
  `verbose_json` (segments, words on request), `diarized_json` (segments with
  speaker letters A, B…). `stream=true` — SSE with `transcript.text.delta` /
  `transcript.text.done`; while recognition runs the server emits
  `: progress …` comments to keep the connection alive.
- GigaAM extensions as plain form fields: `diarize`, `diarization_backend`,
  `num_speakers`, `asr_backend`, `onnx_provider`, `audio_preprocessing`.
- `GET /v1/models`, `GET /v1/models/{id}` — OpenAI-shaped model list with a
  `gigaam` block (backends, ONNX providers, active configuration).
- `Authorization: Bearer <key>` (as the OpenAI SDKs send it); `X-API-Key` is
  still accepted. The hashed `.api_keys` store is unchanged.
- Errors use the OpenAI envelope `{"error": {"message", "type", "param",
  "code"}}` for every status; 429 carries `Retry-After`.
- Works with the official SDKs: set `base_url="http://127.0.0.1:8000/v1"` and
  the key. Reference: `docs/API.md`; the Postman collection is updated.

**MCP server for AI agents**

- Claude Code, Codex, Cursor and any MCP client get GigaAM as a tool set:
  `transcribe` (audio/video by `url`, a path on the server or `audio_base64`
  → text, segments, speakers, SRT/VTT; progress notifications with stage and
  percent while it runs), `summarize` (summary, tasks, terms or a custom
  prompt through the configured LLM provider), `list_models`,
  `list_llm_providers`, `server_status`; resources `gigaam://models`,
  `gigaam://status`; prompts `meeting_notes` (meeting minutes) and
  `subtitles_review` (SRT review).
- Locally: `gigaam mcp` (stdio, ships with the TUI install) —
  `claude mcp add gigaam -- gigaam mcp`; files on the same machine are passed
  by path. Remotely: `/mcp` in `api.py` and in the web panel (Docker image
  included) — `claude mcp add --transport http gigaam
  https://gigaam-site.dubr1k.space/mcp --header "Authorization: Bearer <key>"`;
  the key is the REST API key, without it — 401. In HTTP mode server paths are
  rejected unless `GIGAAM_MCP_ALLOW_PATHS=1` (`GIGAAM_MCP_PATH_ROOT` confines
  them); the base64 limit is `GIGAAM_MCP_MAX_INLINE_MB` (25 MB).
- Agent skills: `skills/gigaam` now covers MCP, and the new
  `skills/gigaam-mcp` is a standalone skill for agents without a local
  install (tool contract, source choice, errors, recipes);
  `gigaam --install-skill` installs both. Reference: `docs/MCP.md`; nginx
  snippet for the SSE stream: `deploy/nginx-mcp-location.conf`.

### Changed (breaking)

- Removed `/api/v1/transcribe`, `/api/v1/transcribe/batch`, `/api/v1/tasks*`,
  `/api/v1/download-batch`, `/api/v1/asr/options`, the task queue and on-disk
  results. The response is now returned synchronously in the same request.
  In-app examples and docs point at the new API; the old API/Postman guides are
  replaced by `docs/API.md`.
- `TASK_CLEANUP_HOURS` and `RATE_LIMIT_API` are no longer read;
  `RATE_LIMIT_UPLOAD` now really limits the transcription request rate.

### Fixed

- The TUI worker exited right after start (“Worker exited”): the startup probe
  of CLI providers handed the worker's stdin to child processes, and
  `pi --version` set O_NONBLOCK on it — the worker mistook EAGAIN for EOF.
  Probes and input-less LLM runs now get `DEVNULL`, and the worker reads its
  own stdin robustly.
- The first Esc during processing sends the worker a graceful cancel, as the
  hint promises; the second one restarts it.
- The API validates uploads before reading the body: a request without an
  auth header or with `Content-Length` above the limit is rejected at once,
  nothing is written to disk.

### What to check

- `gigaam --update` → `gigaam --version` prints `gigaam-tui v2.5.0`; `gigaam`
  opens in Russian, `/lang en` switches and the desktop app sees the same value.
- Mouse clicks on tabs and parameter rows; `?` help; `/mouse off`.
- `python api.py`, then `curl http://127.0.0.1:8000/v1/audio/transcriptions
  -H "Authorization: Bearer $KEY" -F file=@a.mp3 -F model=whisper-1`;
  `-F response_format=srt`, `-F stream=true`; the `openai` SDK with
  `base_url=".../v1"`.
- The web panel (`web/`) and the Docker image behave as before — they do not
  use `api.py`; both now serve `/mcp`, and the web panel prints the key on
  first start.
- `claude mcp add gigaam -- gigaam mcp`, then `/mcp` in Claude Code lists
  `gigaam`; ask the agent to call `server_status` and `transcribe` with a path
  to a short file. Remotely — `claude mcp add --transport http …` with the key
  and `transcribe(url=…)` on a short clip.
