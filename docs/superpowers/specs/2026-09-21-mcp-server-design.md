# MCP-сервер GigaAM — дизайн

Дата: 2026-09-21. Статус: одобрен пользователем («делай все так, и все делаем в
2.5.0, нужна нормальная документация»). Ветка: `mcp` (от `main` @ 42cfa1f, после
слияния TUI 2.0 и OpenAI-совместимого API). Входит в релиз 2.5.0.

## Цель

Дать ИИ-агентам и харнессам (Claude Code, Codex, Cursor, OpenWebUI, n8n, любые
MCP-клиенты) полноценный доступ к GigaAM через Model Context Protocol:
распознать аудио/видео (по URL, по пути, из base64), получить текст, сегменты,
субтитры и говорящих, прогнать транскрипт через LLM (выжимка, задачи, термины,
свой промпт), узнать модели и состояние сервера. Один и тот же сервер работает
локально (stdio, `gigaam mcp`) и удалённо (Streamable HTTP `/mcp` в REST API и
в веб-панели → `https://gigaam-site.dubr1k.space/mcp`).

## Не-цели

- Очередь/история задач, скачивание файлов результатов — вызов MCP синхронный
  и отдаёт результат сразу.
- OAuth/динамическая регистрация клиентов — ключа API достаточно (§4).
- Батч — агент вызывает `transcribe` в цикле.
- Realtime/Live — нет.

## 1. Архитектура

```
src/services/mcp_server.py      MCPServer (SDK mcp 2.x; в 1.x звался FastMCP) — tools/resources/prompts, транспорт-агностичен
src/services/mcp_backend.py     «бэкенд» — Protocol с одной реализацией на процесс:
                                transcribe(...), summarize(...), models(), status()
src/services/api_keys.py        хранилище .api_keys (вынос из api.py) — общее для api.py, web_app.py, MCP
src/services/llm_settings.py    LLM-настройки для Python-слоя (user_settings.json / tui_settings.json / .env / env)
src/mcp_server.py               `python -m src.mcp_server` — stdio (по умолчанию) или --http
api.py                          app.mount("/mcp", …)  — тот же model_loader и семафор
web/web_app.py                  app.mount("/mcp", …)  — то же; так /mcp попадает в контейнер gigaam-web
```

- **`mcp_backend.LocalBackend`** — одна реализация, принимающая `model_loader`,
  `stats_manager`, `processing_semaphore`, `upload_dir`, `media_downloader`
  и колбэк прогресса. Внутри — `transcription_service.build_processor` +
  `process_file(..., output_formats=[])` → `result["utterances"]` →
  `transcript_formats.render(...)`; LLM — `llm_service.run_provider`. Это
  ровно тот же путь, что у `POST /v1/audio/transcriptions`, поэтому `api.py`
  выносит свою `blocking()`-часть в `mcp_backend` и вызывает её сам (одна
  реализация, а не две).
- **`mcp_server.build_server(backend) -> MCPServer`** — регистрирует
  tools/resources/prompts и ничего не знает о FastAPI/stdio.
  SDK: `mcp>=2,<3` (`from mcp.server.mcpserver import MCPServer, Context`;
  `Context.report_progress(progress, total, message)`; тесты через
  `mcp.shared.memory.create_client_server_memory_streams` + `ClientSession`).
- **Транспорты**:
  - stdio: `python -m src.mcp_server` (загружает модель сам, как `api.py`);
    лаунчер `gigaam mcp` (и `gigaam mcp --http --port N` для локального
    HTTP). Логи — только в stderr/файл, stdout принадлежит протоколу.
  - Streamable HTTP: `server.streamable_http_app(streamable_http_path="/",
    stateless_http=True, max_request_body_size=64 MiB,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`
    монтируется под `/mcp` в `api.py` и `web/web_app.py` (`app.mount("/mcp",
    guard(mcp_app))`). `stateless_http=True` — чтобы nginx/несколько воркеров
    не ломали сессии и не требовался event store; отключённая DNS-rebinding
    защита — потому что публичный хост `gigaam-site.dubr1k.space` и локальные
    адреса различаются, а авторизация всё равно по ключу. Lifespan
    `session_manager.run()` подключается к lifespan приложения.
    Зависимость `mcp>=2,<3` добавляется в `requirements.txt`,
    `requirements-tui.txt` и в Docker-образ (он ставит `requirements.txt`).

## 2. Инструменты

Все параметры — JSON-схема из type hints/pydantic; описания на английском
(машинный контракт), с примерами.

### `transcribe`

| Параметр | Тип | Описание |
|---|---|---|
| `url` | str? | http/https-ссылка (прямой файл или страница, которую понимает yt-dlp) — через `MediaDownloader` |
| `path` | str? | путь к файлу, видимому серверу (локальный stdio-режим; в HTTP-режиме допускается только если `GIGAAM_MCP_ALLOW_PATHS=1`) |
| `audio_base64` | str? | содержимое файла в base64, ≤ `GIGAAM_MCP_MAX_INLINE_MB` (по умолчанию 25); `filename` обязателен |
| `filename` | str? | имя для base64-источника (расширение из `SUPPORTED_FORMATS`) |
| `model` | str = default | id модели или алиас (`whisper-1` → модель по умолчанию), как в REST |
| `language` | str? | как в REST (проброс в `verbose`) |
| `format` | `text \| json \| verbose \| diarized \| srt \| vtt` = `json` | форма ответа (= `response_format` REST: `json`, `verbose_json`, `diarized_json`) |
| `word_timestamps` | bool = false | слова в `verbose` |
| `diarize` | bool = false | диаризация; для `diarized` включается автоматически |
| `diarization_backend` | str = `pyannote` | `pyannote \| sortformer \| onnx` |
| `num_speakers` | int? | ≥ 1; с `sortformer` — ошибка |
| `audio_preprocessing` | str? | `off \| auto \| light \| denoise` |
| `asr_backend`, `onnx_provider` | str? | как в REST |

Ровно один из `url` / `path` / `audio_base64`. Результат — `dict`:
`{"text", "duration", "language", "segments": [...]?, "words": [...]?,
"subtitles": str?, "usage": {"type": "duration", "seconds"}, "source":
{"kind": "url|path|inline", "name"}}` — `segments/words` по `transcript_formats`
(verbose/diarized), `subtitles` для srt/vtt. Во время работы — MCP progress
notifications (`ctx.report_progress(current, total, message)`) со стадией
(`downloading`, `preparing`, `conversion`, `preprocessing`, `transcription`,
`diarization`) и процентом. Ошибки — `McpError`/`ToolError` с тем же текстом и
кодом, что в REST (`unsupported_file`, `file_too_large`, `model_not_found`…),
код кладётся в начало сообщения: `"[unsupported_file] Unsupported file type…"`.

### `summarize`

| Параметр | Тип | Описание |
|---|---|---|
| `text` | str | транскрипт (обязателен) |
| `mode` | `summary \| tasks \| terms \| custom` = `summary` | промпты из `llm_worker_service.PROMPTS` |
| `prompt` | str? | обязателен при `custom` |
| `provider` | str? | переопределить провайдера (`API`, `Claude Code`, `Codex`, `OpenCode`, `Pi`, `oh-my-pi`, `Other`); по умолчанию из настроек |
| `model` | str? | переопределить модель |

Результат `{"mode", "provider", "model", "answer"}`. Настройки LLM —
`llm_settings.resolve()` (§5).

### `list_models` → как `GET /v1/models` (`data[]` + `gigaam{backends, onnx_providers, active}`).
### `list_llm_providers` → `cli_tools.scan()` в формате `llm_tools` воркера (провайдер, найден ли, версия, путь) + `API` с признаком «ключ задан».
### `server_status` → `{"version", "runtime", "asr": health_service.asr_health(...), "busy": семафор занят/свободно, "limits": {max_file_mb, max_inline_mb, max_concurrent}}`.

## 3. Ресурсы и промпты

- Ресурсы `gigaam://models`, `gigaam://status` — JSON тех же данных (для
  клиентов, которые читают ресурсы, а не зовут tools).
- Промпт `meeting_notes(transcript: str, language: str = "ru")` — шаблон
  «транскрипт → протокол встречи (решения, задачи, сроки, открытые вопросы)».
- Промпт `subtitles_review(srt: str)` — «проверь субтитры на разрывы фраз и
  предложи правки». Два промпта достаточно; больше — по запросам.

## 4. Авторизация и лимиты

- `src/services/api_keys.py`: `KeyStore(path)` с `load()`, `save()`,
  `verify(key) -> bool` (constant-time), `create_default() -> str`,
  миграция plaintext → SHA-256; `api.py` переезжает на него без изменения
  формата файла. `web_app.py` получает тот же `KeyStore`
  (`API_KEYS_FILE`, в контейнере — `/data/.api_keys`, persist-том; при первом
  старте ключ печатается в лог с рамкой, как у `api.py`).
- HTTP `/mcp`: собственный pure-ASGI guard (как `_UploadGuard` в `api.py`) в
  точке монтирования требует `Authorization: Bearer <key>` (или `X-API-Key`),
  проверяет по `KeyStore`; SDK-шный `token_verifier`/`AuthSettings` не
  используем — он предполагает OAuth resource-server метаданные
  (`issuer_url`), чего у нас нет. Без/с неверным ключом — `401` JSON
  `{"error": {"message", "type": "authentication_error", "code":
  "invalid_api_key"}}`. stdio — без ключа.
- Лимиты: размер входа (`MAX_FILE_SIZE` для url/path, `GIGAAM_MCP_MAX_INLINE_MB`
  для base64), `processing_semaphore` общий с REST/веб; rate-limit на `/mcp`
  не вводим (сессии долгие, лимитируется семафором).
- В HTTP-режиме `path` по умолчанию запрещён (иначе удалённый агент читает
  файлы сервера); `GIGAAM_MCP_ALLOW_PATHS=1` включает, с проверкой, что путь
  внутри `GIGAAM_MCP_PATH_ROOT` (если задан).

## 5. Настройки LLM для Python-слоя

`llm_settings.resolve(overrides: dict | None) -> dict` собирает тот же словарь,
что `worker::llm_settings_from` в TUI: провайдер/модель/URL/температура из
`user_settings.json` (ключи `llm_provider`, `llm_model`, `llm_api_url`,
`llm_temperature`, `llm_allow_tools`, `llm_*_path/_provider/_args`), иначе из
`tui_settings.json`, `api_key` из `.env`/`LLM_API_KEY`; переменные окружения
`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_URL`, `LLM_API_KEY`, `LLM_TEMPERATURE`
имеют приоритет (так контейнер настраивается без файлов). Пути CLI-провайдеров
— из `cli_tools.scan()`.

## 6. Установка для агентов

- `gigaam mcp` в `scripts/tui/gigaam-launcher.sh` → `"$VENV/bin/python" -m
  src.mcp_server` (stdio); `gigaam mcp --http [--host H] [--port N]`.
- `gigaam --install-skill` (и установщик) дописывают в `skills/gigaam/SKILL.md`
  раздел MCP и печатают готовый фрагмент для `.mcp.json` /
  `claude mcp add gigaam -- gigaam mcp`; для удалённого —
  `claude mcp add --transport http gigaam https://gigaam-site.dubr1k.space/mcp
  --header "Authorization: Bearer …"`.
- `docs/MCP.md` (русский, код на английском): что это, установка локально
  (Claude Code / Codex / Cursor / OpenWebUI — по одному фрагменту конфига),
  удалённое подключение с ключом, полный справочник tools/resources/prompts
  с примерами вызовов и ответов, лимиты, ошибки, переменные окружения,
  деплой за nginx. Ссылки из README/README_EN/START_HERE/API.md; запись в
  CHANGELOG 2.5.0 и RELEASE_NOTES_2.5.0 (обе языковые части).

## 7. Деплой на `home`

- Образ пересобирается из Syncthing-копии (`docker compose build gigaam-web && up -d`).
- `docker-compose.yml`: переменная `API_KEYS_FILE=/data/.api_keys` (persist);
  ничего больше (порт тот же).
- nginx `gigaam-site.dubr1k.space`: `location /mcp { proxy_pass
  http://127.0.0.1:8001/mcp; proxy_http_version 1.1; proxy_buffering off;
  proxy_read_timeout 3600s; proxy_set_header Connection ""; }` — SSE-поток
  Streamable HTTP. Правка через `sudo`, `nginx -t`, `reload`.
- Проверка: `curl -N -X POST https://gigaam-site.dubr1k.space/mcp -H
  "Authorization: Bearer $KEY" -H "Accept: application/json, text/event-stream"
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",…}'` → `serverInfo`;
  затем `claude mcp add --transport http …` и вызов `server_status` +
  `transcribe(url=…)` с короткого ролика.

## 8. Тесты

- `tests/test_mcp_server.py`: in-memory клиент SDK (`mcp.shared.memory.
  create_connected_server_and_client_session` или `Client(server)` FastMCP) с
  поддельным `backend` — список tools/resources/prompts, `transcribe` по
  каждому источнику (url через поддельный downloader, path, base64; взаимное
  исключение; лимит base64; запрет path в HTTP-режиме), формат результата на
  каждый `format`, progress-уведомления доходят, `summarize` через
  поддельный `run_provider`, ошибки с кодами.
- `tests/test_mcp_http.py`: TestClient `api.app` и `web_app.app` — `/mcp`
  без ключа → 401 в конверте, с ключом → `initialize` отвечает.
- `tests/test_api_keys.py`: KeyStore (миграция, 0600, verify constant-time) —
  переезд существующих проверок из `test_api_security.py`.
- `tests/test_llm_settings.py`: приоритет env > user_settings > tui_settings.
- `tests/test_docs_mcp.py`: `docs/MCP.md` покрывает все tools/resources/prompts
  по именам из `mcp_server` (сканирует зарегистрированные имена).
- Живая проверка (контроллер): stdio через `claude mcp add` локально —
  `server_status`, `transcribe(path=speech.wav)`, `summarize`; HTTP локально
  через `api.py`; после деплоя — по публичному URL.

## 9. Риски

- Версия SDK `mcp` 2.x против 1.x: API `FastMCP`/`streamable_http_app` —
  фиксируем `mcp>=1.10,<3` и проверяем импорт в тестах.
- Долгий вызов через nginx/клиента: progress-уведомления держат поток; в
  docs — рекомендация таймаута ≥ 1 ч у клиента для длинных записей.
- Контейнер: `python -m src.mcp_server` там не нужен — только mount в
  `web_app`; но `web_app` теперь читает `.api_keys` — при отсутствии файла
  создаёт и печатает ключ (том `/data` persist, так что один раз).
- Base64 в контексте агента дорог — документируем «для клипов до пары минут,
  иначе URL/path».
