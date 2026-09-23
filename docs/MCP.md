# MCP-сервер GigaAM

GigaAM v3 Transcriber отдаёт распознавание речи ИИ-агентам по [Model Context
Protocol](https://modelcontextprotocol.io): Claude Code, Codex, Cursor, Open
WebUI, n8n и любой другой MCP-клиент могут вызвать `transcribe` (аудио/видео →
текст, сегменты, субтитры, говорящие), `summarize` (транскрипт → выжимка,
задачи, термины или свой промпт), узнать модели и состояние сервера. Один и тот
же сервер работает в двух режимах:

- **локально, stdio** — `gigaam mcp` (или `python -m src.mcp_server`): агент
  запускает процесс сам; ASR-веса загружаются только на время каждого `transcribe`
  и освобождаются в конце, включая ошибку обработки. Ключ не нужен, локальные
  файлы можно передавать по пути;
- **удалённо, Streamable HTTP** — `/mcp` в REST API (`api.py`) и в веб-панели
  (`web/web_app.py`, а значит и в Docker-образе); публичный адрес —
  `https://gigaam-site.dubr1k.space/mcp`. Каждый запрос — с API-ключом.

Содержание:

- [Быстрый старт локально](#быстрый-старт-локально)
- [Удалённое подключение](#удалённое-подключение)
- [Инструменты](#инструменты)
- [Ресурсы и промпты](#ресурсы-и-промпты)
- [Источники аудио и лимиты](#источники-аудио-и-лимиты)
- [Прогресс и длительные вызовы](#прогресс-и-длительные-вызовы)
- [Ошибки](#ошибки)
- [Переменные окружения](#переменные-окружения)
- [Деплой за nginx](#деплой-за-nginx)
- [Безопасность](#безопасность)
- [Скиллы для агентов](#скиллы-для-агентов)

## Быстрый старт локально

Сервер входит в установку TUI (`scripts/install_tui.sh`) — команда `gigaam mcp`
запускает его по stdio из окружения TUI. Из репозитория то же самое делает
`python -m src.mcp_server` (зависимость `mcp>=2,<3` из `requirements.txt`).

```text
gigaam mcp [--http] [--host 127.0.0.1] [--port 8765] [--config-dir DIR]
```

- без флагов — stdio; stdout занят протоколом, все логи уходят в stderr и в
  файл журнала;
- `--http` — тот же сервер как Streamable HTTP на `http://127.0.0.1:8765/mcp`
  за ключом из `API_KEYS_FILE` (для локального HTTP-клиента, который не умеет
  stdio). Для `gigaam mcp --http` ключ лежит в `$PREFIX/repo/.api_keys`
  (по умолчанию `~/.local/share/gigaam-tui/repo/.api_keys`), при первом старте
  файл создаётся и ключ печатается в stdout один раз — сохраните его;
- `--config-dir DIR` — откуда брать настройки LLM для `summarize`
  (`user_settings.json` / `tui_settings.json` / `.env`); по умолчанию —
  каталог настроек приложения или `GIGAAM_CONFIG_DIR`.

Первый запуск скачивает модель (сотни мегабайт) — как и у `gigaam transcribe`.

**Claude Code** (stdio):

```bash
claude mcp add gigaam -- gigaam mcp
```

или в `.mcp.json` проекта:

```json
{"mcpServers": {"gigaam": {"command": "gigaam", "args": ["mcp"]}}}
```

**Codex** — `~/.codex/config.toml`:

```toml
[mcp_servers.gigaam]
command = "gigaam"
args = ["mcp"]
```

**Cursor** — `.cursor/mcp.json` (или глобальный `~/.cursor/mcp.json`):

```json
{"mcpServers": {"gigaam": {"command": "gigaam", "args": ["mcp"]}}}
```

Если `gigaam` не в `PATH` клиента, укажите полный путь `~/.local/bin/gigaam`
или запускайте из репозитория: `"command": "/path/to/.venv/bin/python",
"args": ["-m", "src.mcp_server"]` с `"cwd"` в корне проекта.

Проверка: в Claude Code — `/mcp` показывает сервер `gigaam` со списком
инструментов; попросите агента «вызови `server_status`».

## Удалённое подключение

`/mcp` смонтирован в `api.py` (порт 8000) и в веб-панели (внутри контейнера —
порт 8000, на хосте `docker-compose.yml` пробрасывает его как `127.0.0.1:8001`,
снаружи — `https://gigaam-site.dubr1k.space/mcp`). Транспорт —
Streamable HTTP без сессий (`stateless`), поэтому за nginx и с несколькими
воркерами работает без общего хранилища.

Каждый запрос должен нести ключ: `Authorization: Bearer <key>` (как в SDK
OpenAI) или `X-API-Key: <key>`. Ключи — те же, что у REST API (`.api_keys`,
хранятся только SHA-256 хэши); при первом старте `api.py` или веб-панели
первый ключ печатается в лог один раз. Без ключа или с неверным — `401` в
конверте OpenAI:

```json
{"error": {"message": "Missing API key. Send 'Authorization: Bearer <key>' or 'X-API-Key: <key>'.",
           "type": "authentication_error", "param": null, "code": "invalid_api_key"}}
```

**Claude Code**:

```bash
claude mcp add --transport http gigaam https://gigaam-site.dubr1k.space/mcp \
  --header "Authorization: Bearer gam_..."
```

**Codex** — `~/.codex/config.toml`:

```toml
[mcp_servers.gigaam]
url = "https://gigaam-site.dubr1k.space/mcp"
http_headers = { Authorization = "Bearer gam_..." }   # или bearer_token_env_var = "GIGAAM_API_KEY"
tool_timeout_sec = 3600                                # длинные записи
```

**Cursor** — `.cursor/mcp.json`:

```json
{"mcpServers": {"gigaam": {"url": "https://gigaam-site.dubr1k.space/mcp",
                           "headers": {"Authorization": "Bearer gam_..."}}}}
```

**curl** (ручная проверка: `initialize` должен вернуть `serverInfo`):

```bash
curl -N -X POST https://gigaam-site.dubr1k.space/mcp \
  -H "Authorization: Bearer gam_..." \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18",
       "capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

Ответ приходит либо JSON, либо SSE-потоком (`data: {...}`) — зависит от
заголовка `Accept`. В удалённом режиме источник аудио — `url` или
`audio_base64`; `path` отклоняется (см. [Безопасность](#безопасность)).

## Инструменты

Все параметры описаны в JSON-схеме инструментов (`tools/list`); ниже — та же
информация в виде таблиц. Описания в схеме — на английском, это машинный
контракт.

### `transcribe`

Распознаёт аудио- или видеозапись. Ровно один источник: `url`, `path` или
`audio_base64`.

| Параметр | Тип | По умолчанию | Описание |
|---|---|---|---|
| `url` | string | — | http(s)-ссылка на медиафайл или страницу, которую понимает yt-dlp; сервер скачивает сам. Предпочтительно для удалённого сервера. |
| `path` | string | — | Путь к файлу в файловой системе сервера. Только stdio-сервер или HTTP с `GIGAAM_MCP_ALLOW_PATHS=1`. |
| `audio_base64` | string | — | Содержимое файла в base64 для коротких клипов (действует лимит `max_inline_mb`). Требует `filename`. |
| `filename` | string | — | Имя файла с расширением для `audio_base64` (`clip.wav`, `call.mp3`). |
| `model` | string | `v3_e2e_rnnt` | Id модели или алиас (`whisper-1`, `gigaam` и другие алиасы → модель по умолчанию); список — `list_models`. |
| `language` | string | `ru` | Подсказка языка, возвращается в ответе как есть; GigaAM распознаёт русский. |
| `format` | `text` \| `json` \| `verbose` \| `diarized` \| `srt` \| `vtt` | `json` | Форма ответа (см. ниже). `text` и `json` возвращают один и тот же объект (текст + длительность + `usage`); `text` оставлен для симметрии с REST. |
| `word_timestamps` | bool | `false` | Добавить `words` в `verbose`. |
| `diarize` | bool | `false` | Разметить говорящих; для `format="diarized"` включается автоматически. |
| `diarization_backend` | `pyannote` \| `sortformer` \| `onnx` | `pyannote` | `pyannote` требует `HF_TOKEN` на сервере. |
| `num_speakers` | int ≥ 1 | — | Ожидаемое число говорящих; с `sortformer` не поддерживается. |
| `audio_preprocessing` | `off` \| `auto` \| `light` \| `denoise` | настройка сервера | Подготовка звука. |
| `asr_backend` | string | настройка сервера | Движок (`torch`, `onnx`, …). |
| `onnx_provider` | string | настройка сервера | Провайдер ONNX при `asr_backend="onnx"` (`cpu`, `cuda`, …). |

Ответ — объект; поля зависят от `format`:

| Поле | Когда | Содержимое |
|---|---|---|
| `text` | всегда | Полный текст. |
| `duration` | всегда | Длительность записи, с. |
| `language` | всегда | Значение `language` или `ru`. |
| `usage` | всегда | `{"type": "duration", "seconds": N}` — как в OpenAI. |
| `source` | всегда | `{"kind": "url" \| "path" \| "inline", "name": "<имя файла>"}`. |
| `segments` | `verbose`, `diarized` | Сегменты `{"id", "start", "end", "text", …}`; в `diarized` — c `"speaker": "A"/"B"/…`. |
| `words` | `verbose` + `word_timestamps` | Слова с таймкодами. |
| `diarization` | `verbose`, `diarized` | `{"requested": bool, "applied": bool}` — если `applied=false`, говорящие в `segments` заглушки (`A` у всех). |
| `subtitles` | `srt`, `vtt` | Субтитры одной строкой. |

Пример вызова (Claude Code / любой клиент — параметры JSON):

```json
{"name": "transcribe",
 "arguments": {"url": "https://example.com/call.mp3", "format": "diarized", "num_speakers": 2}}
```

```json
{"text": "Здравствуйте, это по поводу заказа. Да, слушаю вас.",
 "duration": 12.4, "language": "ru", "usage": {"type": "duration", "seconds": 13},
 "segments": [
   {"id": 0, "type": "transcript.text.segment", "start": 0.0, "end": 4.1, "speaker": "A",
    "text": "Здравствуйте, это по поводу заказа."},
   {"id": 1, "type": "transcript.text.segment", "start": 4.3, "end": 6.0, "speaker": "B",
    "text": "Да, слушаю вас."}],
 "diarization": {"requested": true, "applied": true},
 "source": {"kind": "url", "name": "call.mp3"}}
```

Субтитры:

```json
{"name": "transcribe", "arguments": {"path": "/Users/me/lecture.mp4", "format": "srt"}}
```

```json
{"text": "…", "duration": 3600.2, "language": "ru", "usage": {"type": "duration", "seconds": 3601},
 "subtitles": "1\n00:00:00,000 --> 00:00:04,100\nЗдравствуйте…\n\n2\n…",
 "source": {"kind": "path", "name": "lecture.mp4"}}
```

### `summarize`

Прогоняет транскрипт через LLM, настроенный на сервере (API или CLI-агент —
те же настройки, что у TUI и настольного приложения).

| Параметр | Тип | По умолчанию | Описание |
|---|---|---|---|
| `text` | string | — | Транскрипт (обязателен, не пустой). |
| `mode` | `summary` \| `tasks` \| `terms` \| `custom` | `summary` | Выжимка, список задач, глоссарий или свой промпт. |
| `prompt` | string | — | Инструкция для `mode="custom"`; иначе игнорируется. |
| `provider` | string | настройки сервера | `API`, `Claude Code`, `Codex`, `OpenCode`, `Pi`, `oh-my-pi`, `Other`; список с доступностью — `list_llm_providers`. |
| `model` | string | настройки сервера | Имя модели для выбранного провайдера. |

```json
{"name": "summarize", "arguments": {"text": "<транскрипт>", "mode": "tasks"}}
```

```json
{"mode": "tasks", "provider": "API", "model": "gpt-4o-mini",
 "answer": "1. Прислать смету до пятницы — Иван.\n2. …"}
```

### `list_models`

Без параметров. Тот же ответ, что `GET /v1/models` REST API: `data[]` с
`id`, `description`, `default`, `aliases` и блок `gigaam` — `backends`,
`onnx_providers`, `active` (текущая конфигурация загрузчика).

```json
{"object": "list",
 "data": [{"id": "v3_e2e_rnnt", "object": "model", "created": 0, "owned_by": "gigaam",
           "description": "…", "default": true, "aliases": ["gigaam", "gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"]},
          {"id": "multilingual_ctc", "…": "…"}, {"id": "multilingual_large_ctc", "…": "…"}],
 "gigaam": {"backends": ["torch", "onnx"], "onnx_providers": ["auto", "cpu", "…"], "active": {"…": "…"}}}
```

### `list_llm_providers`

Без параметров. Какие CLI-провайдеры установлены на сервере и настроен ли
ключ API — чтобы выбрать `provider` для `summarize`.

```json
{"providers": [{"name": "Claude Code", "available": true, "version": "2.1.0", "path": "/usr/local/bin/claude"},
               {"name": "Codex", "available": false, "version": null, "path": null}],
 "api": {"configured": true, "model": "gpt-4o-mini"}}
```

### `server_status`

Без параметров. Версия, рантайм, состояние ASR-модели, занятость и лимиты.
`busy.active` — занятые слоты общего семафора процесса: в `api.py` и веб-панели
он один на REST, задачи панели и `/mcp`, так что число включает чужие задачи.
Вызывайте перед большой задачей: `busy.active == busy.max` означает, что
`transcribe` встанет в очередь за семафором.

В локальном stdio постоянный загрузчик не держит весов: `asr.loader_loaded=false`
при `asr.error=null` — ожидаемо и не запрещает вызов `transcribe`.
Каждое задание владеет отдельной моделью до завершения обработки; `busy.active`
показывает выполняющиеся задания. Статус, каталог моделей и `summarize` не
загружают ASR. Пример ниже относится к серверу с постоянной моделью
(`--http`, REST или веб-панель); их жизненный цикл не изменён.

```json
{"version": "2.5.0",
 "runtime": {"platform": "macOS-26.0-arm64", "…": "…"},
 "asr": {"loader_loaded": true, "active_backend": "torch", "model": "v3_e2e_rnnt", "device": "mps", "…": "…"},
 "busy": {"active": 0, "max": 3},
 "limits": {"max_file_mb": 2048.0, "max_inline_mb": 25.0, "max_concurrent": 3}}
```

## Ресурсы и промпты

Ресурсы — те же данные, что у инструментов, для клиентов, которые читают
`resources/read`, а не зовут tools (оба `application/json`):

| URI | Содержимое |
|---|---|
| `gigaam://models` | Как `list_models`. |
| `gigaam://status` | Как `server_status`. |

Промпты (`prompts/get`) возвращают готовое сообщение пользователя для модели
агента — сервер сам LLM не вызывает:

| Промпт | Аргументы | Что даёт |
|---|---|---|
| `meeting_notes` | `transcript` (обязателен), `language` = `ru` \| `en` | Протокол встречи: кратко о встрече, решения, таблица задач (ответственный, срок), открытые вопросы, следующие шаги. |
| `subtitles_review` | `srt` (обязателен) | Проверка SRT: разрывы фраз между блоками, длина блоков, пунктуация, сомнительные слова; ответ — список проблем и исправленный SRT. |

В Claude Code промпты доступны как `/mcp__gigaam__meeting_notes` и
`/mcp__gigaam__subtitles_review`.

## Источники аудио и лимиты

| Источник | Когда использовать | Лимит |
|---|---|---|
| `url` | Всё, что лежит в сети: прямые ссылки на файлы, YouTube и другие страницы, которые понимает yt-dlp. Единственный вариант для удалённого сервера, если файл не короткий. | `MAX_FILE_SIZE` (по умолчанию 2 ГБ) — проверяется при скачивании. |
| `path` | Файл на машине сервера: stdio-режим (агент и сервер на одной машине) или HTTP с `GIGAAM_MCP_ALLOW_PATHS=1`. `~` раскрывается. | `MAX_FILE_SIZE`; при `GIGAAM_MCP_PATH_ROOT` путь должен лежать внутри корня. |
| `audio_base64` + `filename` | Короткие клипы, когда ни URL, ни путь недоступны. Base64 дорог для контекста агента — до пары минут звука. | `GIGAAM_MCP_MAX_INLINE_MB` (по умолчанию 25 МБ), значение отдаёт `server_status().limits.max_inline_mb`. |

Расширение файла (для `path` — реальное, для `audio_base64` — из `filename`,
для `url` — скачанного файла) должно быть одним из поддерживаемых: `mp3 wav
m4a aac mp4 avi mov mkv webm flac ogg wma qta 3gp`. Одновременно
обрабатывается `MAX_CONCURRENT_TASKS` задач (семафор общий с REST API и
веб-панелью); остальные вызовы ждут.

## Прогресс и длительные вызовы

Распознавание идёт примерно в реальном времени на CPU и быстрее на GPU/MPS:
часовая запись — минуты. Пока `transcribe` работает, сервер шлёт MCP progress
notifications (`notifications/progress` с `progress` 0–100, `total` 100 и
`message` — стадия: `downloading`, `preparing`, `conversion`,
`preprocessing`, `transcription`, `diarization`, `export`, `finalizing`), а
результат приходит одним ответом в конце.

Клиенту нужно держать запрос открытым: таймаут вызова инструмента — не меньше
часа для длинных записей (Claude Code — переменная `MCP_TOOL_TIMEOUT` в
миллисекундах; Codex — `tool_timeout_sec` у сервера в `config.toml`; свой
клиент на SDK — `read_timeout_seconds` у `call_tool`). Повторять вызов по
таймауту бессмысленно — сервер продолжит первую задачу до конца и займёт
второй слот семафора. За прокси нужны отключённая буферизация и длинный
`proxy_read_timeout` (см. [Деплой за nginx](#деплой-за-nginx)); при
`notifications/cancelled` сервер освобождает слот, когда текущий шаг закончит
работу, и удаляет временные файлы.

## Ошибки

Ошибка инструмента приходит как результат с `isError: true`; текст содержит
код в квадратных скобках — те же коды, что в REST API. Транспорт добавляет
префикс (`Error executing tool transcribe: [file_too_large] …`), поэтому ищите
`[code]` в тексте, а не проверяйте его начало:

| Код | Причина | Что делать |
|---|---|---|
| `invalid_request` | Не ровно один источник; `audio_base64` без `filename` или не base64; пустой `text` в `summarize`; неизвестный `format`. | Исправить параметры. |
| `unsupported_parameter` | Неизвестный `diarization_backend`, `audio_preprocessing`, `mode` или `provider`; `num_speakers` < 1 или вместе с `sortformer`; недопустимая пара `asr_backend`/`onnx_provider`. | Взять значение из `list_models` / `list_llm_providers`. |
| `model_not_found` | `model` не из реестра. | `list_models`. |
| `model_not_loaded` | ASR-модель на сервере не загружена. | Проверить `server_status().asr`, лог сервера. |
| `diarization_unavailable` | `pyannote` без `HF_TOKEN` на сервере. | `diarization_backend="sortformer"` или `"onnx"`. |
| `unsupported_file` | Расширение не поддерживается. | Сконвертировать или указать правильное `filename`. |
| `file_too_large` | Больше `MAX_FILE_SIZE` (url/path) или `max_inline_mb` (base64). | Передать `url`/`path` вместо base64, порезать файл. |
| `file_not_found` | `path` не существует или не файл. | Проверить путь на машине **сервера**. |
| `paths_not_allowed` | `path` на HTTP-сервере без `GIGAAM_MCP_ALLOW_PATHS=1`. | Использовать `url` или `audio_base64`. |
| `path_outside_root` | `path` вне `GIGAAM_MCP_PATH_ROOT`. | Положить файл внутрь корня. |
| `download_failed` | Не удалось скачать `url` (сеть, yt-dlp, пусто). | Проверить ссылку; текст ошибки содержит причину. |
| `prompt_required` | `mode="custom"` без `prompt`. | Передать `prompt`. |
| `llm_failed` | Провайдер LLM вернул ошибку (нет ключа, CLI упал). | `list_llm_providers`, лог сервера. |
| `processing_failed` | Обработка упала на сервере. | Лог сервера. |
| `internal_error` | Непредвиденное исключение; детали только в логе. | Лог сервера. |

Ошибки уровня транспорта (`401 invalid_api_key`, `503 service_unavailable`
пока не прошёл lifespan) приходят HTTP-статусом в конверте OpenAI, а не
результатом инструмента.

## Переменные окружения

Читаются процессом, в котором работает сервер: `gigaam mcp` /
`python -m src.mcp_server`, `api.py` или веб-панель (в контейнере — через
`docker-compose.yml`).

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `GIGAAM_MCP_ALLOW_PATHS` | не задана | `1` — разрешить источник `path` в HTTP-режиме. В stdio-режиме пути разрешены всегда. |
| `GIGAAM_MCP_PATH_ROOT` | не задана | Каталог, внутри которого должны лежать файлы для `path` (после раскрытия `~` и симлинков); вне корня → `path_outside_root`. |
| `GIGAAM_MCP_MAX_INLINE_MB` | `25` | Лимит `audio_base64` в мегабайтах (десятичная дробь допустима). Лимит тела HTTP-запроса выводится из него: `max(64 МиБ, лимит × 4/3 + 1 МиБ)`; при большем значении поднимите `client_max_body_size` в nginx до того же числа (см. [Деплой за nginx](#деплой-за-nginx)), иначе nginx ответит 413 до сервера. |
| `API_KEYS_FILE` | `.api_keys` | Файл с SHA-256 хэшами ключей для `/mcp` (общий с REST API; в контейнере `/data/.api_keys`). |
| `MAX_FILE_SIZE` | `2147483648` | Лимит `url`/`path` в байтах (общий с REST API). |
| `MAX_CONCURRENT_TASKS` | `3` | Ёмкость семафора транскрибации (общий с REST API). |
| `UPLOAD_DIR` | `uploads` | Куда кладутся временные каталоги `mcp_*` (удаляются после ответа). |
| `HF_TOKEN` | пусто | Нужен для `diarization_backend=pyannote`. |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_URL`, `LLM_API_KEY`, `LLM_TEMPERATURE` | из настроек | Переопределяют настройки LLM для `summarize` (приоритет над `user_settings.json` / `tui_settings.json`). |

## Деплой за nginx

Streamable HTTP отвечает SSE-потоком, который живёт всё время вызова, поэтому
прокси не должен буферизовать ответ и рвать соединение по короткому таймауту.
Готовый фрагмент — `deploy/nginx-mcp-location.conf`:

```nginx
location /mcp {
    proxy_pass http://127.0.0.1:8001/mcp;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    client_max_body_size 64m;
}
```

`8001` — порт веб-панели из `docker-compose.yml`; для `api.py` — `8000`.
`client_max_body_size` должен быть не меньше лимита тела сервера — 64m при
`GIGAAM_MCP_MAX_INLINE_MB` до 45; выше — `лимит × 4/3 + 1` МиБ (например,
`100` → `135m`), иначе nginx отвечает голым 413 вместо `[file_too_large]`.
После правки: `sudo nginx -t && sudo systemctl reload nginx`, затем `curl`
из раздела [Удалённое подключение](#удалённое-подключение) и вызов
`server_status` из клиента.

## Безопасность

- `/mcp` требует ключ на **каждом** запросе — включая `initialize`; сессии
  веб-панели на него не действуют. `OPTIONS` (CORS preflight) пропускается
  без ключа.
- В HTTP-режиме `path` по умолчанию запрещён: иначе любой обладатель ключа
  читал бы файлы сервера. `GIGAAM_MCP_ALLOW_PATHS=1` включает пути, а
  `GIGAAM_MCP_PATH_ROOT` ограничивает их каталогом — задавайте оба, если
  сервер доступен не только вам.
- Ключи не логируются; в `.api_keys` только хэши. Ротация — как у REST API:
  удалите файл (или ненужную строку из него) и перезапустите сервер; без
  файла при старте создаётся и печатается новый ключ.
- Защита от DNS-rebinding в транспорте выключена (публичный хост и адрес
  привязки различаются); доступ ограничивается ключом и nginx.
- Временные файлы (`uploads/mcp_*`) удаляются после ответа и при отмене.

## Скиллы для агентов

В репозитории два скилла, `gigaam --install-skill` и установщик копируют оба в
`~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills` (только в те
каталоги, что уже существуют):

- `skills/gigaam/SKILL.md` — headless-команды `gigaam transcribe` /
  `gigaam llm` плюс раздел про MCP: когда сервер подключён, агент вызывает
  инструменты вместо оболочки;
- `skills/gigaam-mcp/SKILL.md` — самостоятельный скилл для агентов, у
  которых есть только MCP-сервер (локальный или удалённый) и нет `gigaam`
  на машине: контракт инструментов, выбор источника, ошибки, рецепты.
  Скопируйте его на машину агента вручную, если TUI там не установлен.
