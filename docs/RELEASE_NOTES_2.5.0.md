# GigaAM Transcriber 2.5.0

Большой релиз: переработанный терминальный клиент (TUI 2.0) и новый REST API,
совместимый с OpenAI Audio API. Старое REST API `/api/v1/*` удалено — это
несовместимое изменение для тех, кто ходил к серверу напрямую; настольные
приложения, веб-панель и TUI не затронуты.

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
- **Цветовые темы**: `/theme` открывает список из 102 схем, `/theme <имя>`
  включает схему сразу (Tab дописывает имя), строка «Тема» в настройках,
  `--theme NAME` на один запуск; выбор хранится в `tui_settings.json`.
  В комплекте `default`, `mono`, `dark-hermes-pink` и 99 палитр
  [oh-my-pi](https://github.com/can1357/oh-my-pi) (MIT,
  `tui/themes/LICENSE-oh-my-pi`), например `dark-monokai`, `dark-tokyo-night`,
  `light-github`.
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
- `/theme dark-hermes-pink` — розовая схема, `/theme` — список, F3 → строка
  «Тема» показывает имя, `/theme default` возвращает прежний вид.
- `python api.py`, затем `curl http://127.0.0.1:8000/v1/audio/transcriptions
  -H "Authorization: Bearer $KEY" -F file=@a.mp3 -F model=whisper-1` — текст;
  `-F response_format=srt`, `-F stream=true`; `openai` SDK с
  `base_url=".../v1"`.
- Веб-панель (`web/`) и Docker-образ работают как раньше — они не используют
  `api.py`.

---

## English

A major release: the reworked terminal client (TUI 2.0) and a new REST API
compatible with the OpenAI Audio API. The old `/api/v1/*` REST API is removed —
a breaking change for direct API consumers; the desktop apps, web panel and TUI
are unaffected.

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
- Colour themes: `/theme` opens a list of 102 schemes, `/theme <name>` switches
  at once (Tab completes the name), a «Theme» row in Settings, `--theme NAME`
  for one run; the choice is stored in `tui_settings.json`. Bundled: `default`,
  `mono`, `dark-hermes-pink` and 99 palettes from
  [oh-my-pi](https://github.com/can1357/oh-my-pi) (MIT,
  `tui/themes/LICENSE-oh-my-pi`), e.g. `dark-monokai`, `dark-tokyo-night`,
  `light-github`.
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
- `/theme dark-hermes-pink` for the pink scheme, `/theme` for the list, F3 →
  the «Theme» row shows the name, `/theme default` restores the old look.
- `python api.py`, then `curl http://127.0.0.1:8000/v1/audio/transcriptions
  -H "Authorization: Bearer $KEY" -F file=@a.mp3 -F model=whisper-1`;
  `-F response_format=srt`, `-F stream=true`; the `openai` SDK with
  `base_url=".../v1"`.
- The web panel (`web/`) and the Docker image behave as before — they do not
  use `api.py`.
