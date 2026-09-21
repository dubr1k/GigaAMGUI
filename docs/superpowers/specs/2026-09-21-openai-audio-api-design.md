# REST API по образцу OpenAI Audio API — дизайн

Дата: 2026-09-21. Статус: одобрен пользователем («все старое API можешь
удалить и ввести новое»). Ветка: `api-openai` (от `main`), файлы не пересекаются
с `tui-2`.

## Цель

Заменить собственный REST API (`/api/v1/*`, асинхронные задачи, `X-API-Key`)
на API, совместимый с OpenAI Audio API: клиент, написанный под
`openai.audio.transcriptions.create(...)` (официальный SDK, `curl`, любой
«whisper-совместимый» инструмент — Obsidian-плагины, n8n, Home Assistant,
OpenWebUI), должен работать с GigaAM, поменяв только `base_url` и ключ.
Старое API удаляется целиком вместе с документацией и Postman-коллекцией к нему.

## Не-цели

- Перевод (`/v1/audio/translations`) — GigaAM не переводит; эндпоинт отвечает
  ошибкой в формате OpenAI (см. §4).
- Речь из текста (`/v1/audio/speech`) — нет.
- Очередь задач, batch-загрузка, скачивание zip, история задач на диске —
  удаляются. OpenAI-контракт синхронный; долгие файлы держат HTTP-соединение
  (keep-alive через SSE при `stream=true`).
- Realtime/WebSocket-транскрибация — нет (Live-режим остаётся в GUI/TUI).

## 1. Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/v1/audio/transcriptions` | транскрибация одного файла (multipart), синхронно или SSE-стрим |
| `POST` | `/v1/audio/translations` | всегда `400 invalid_request_error` «GigaAM does not translate» |
| `GET` | `/v1/models` | список моделей в формате OpenAI |
| `GET` | `/v1/models/{id}` | одна модель или `404 model_not_found` |
| `GET` | `/health` | без авторизации, как сейчас (статус ASR, версия) |
| `GET` | `/` | краткая JSON-справка: имя, версия, `docs: "/docs"` |
| `GET` | `/docs`, `/openapi.json` | автогенерируемая OpenAPI-документация FastAPI |

Все `/api/v1/*` маршруты удаляются. Никаких редиректов со старых путей — они
отвечают стандартным `404` FastAPI в OpenAI-конверте (§4).

## 2. Авторизация

- Основной способ: `Authorization: Bearer <key>` (так шлёт SDK OpenAI).
- Дополнительно принимается `X-API-Key: <key>` — одна строка кода, оставляет
  рабочими примеры в PyQt/Liquid и старые скрипты пользователей.
- Хранилище ключей не меняется: `.api_keys` с SHA-256 хэшами,
  `load_api_keys/save_api_keys/verify_api_key/_hash_key`, генерация ключа при
  первом старте, constant-time сравнение.
- Отсутствие/неверный ключ → `401` с телом OpenAI-ошибки, `code: "invalid_api_key"`.
- `GET /health`, `/`, `/docs`, `/openapi.json` — без ключа (как сейчас).

## 3. `POST /v1/audio/transcriptions`

`multipart/form-data`. Поля OpenAI + расширения GigaAM (расширения имеют
префиксов нет — так удобнее из `extra_body` SDK; они задокументированы как
«GigaAM extensions» в OpenAPI).

| Поле | Тип | Поведение |
|---|---|---|
| `file` | file, обязательно | аудио/видео из `SUPPORTED_FORMATS`; лимит размера как сейчас (`MAX_FILE_SIZE`); имя проходит `safe_filename` |
| `model` | str, обязательно | id из `GET /v1/models` (`v3_e2e_rnnt`, `multilingual_ctc`, `multilingual_large_ctc`) **или алиас** `whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `gigaam` → модель по умолчанию из настроек. Неизвестное → `404 model_not_found` (как у OpenAI) |
| `language` | str | принимается и игнорируется для `v3_e2e_rnnt` (только русский); для multilingual — передаётся как есть в ответ `verbose_json.language`. Не валидируется по ISO-списку |
| `prompt` | str | принимается, игнорируется (в ответе не отражается) |
| `response_format` | enum | `json` (по умолчанию), `text`, `srt`, `vtt`, `verbose_json`, `diarized_json` |
| `temperature` | float 0..1 | принимается, игнорируется |
| `timestamp_granularities[]` | list | `segment` (по умолчанию), `word`; действует только при `verbose_json`, как у OpenAI |
| `stream` | bool | `true` → SSE (§3.3); несовместимо с `srt`/`vtt`/`text` → `400` |
| `include[]` | list | принимается, игнорируется (logprobs нет) |
| `chunking_strategy` | str/json | принимается, игнорируется — GigaAM всегда режет по VAD |
| `known_speaker_names[]`, `known_speaker_references[]` | list | `400 invalid_request_error` если непусто (не поддерживается) |
| **`diarize`** | bool, расширение | включить диаризацию; при `response_format=diarized_json` включается автоматически |
| **`diarization_backend`** | str, расширение | `pyannote` \| `sortformer`, нормализация через `normalize_diarization_backend` |
| **`num_speakers`** | int ≥1, расширение | как сейчас; с `sortformer` → `400` |
| **`asr_backend`**, **`onnx_provider`** | str, расширение | как в старом `upload_file`: `transcription_service.normalize_asr_selection` |
| **`audio_preprocessing`** | str, расширение | `off` \| `auto` \| `deepfilter` (значения `AUDIO_PREPROCESSING_MODE`); по умолчанию из настроек |

### 3.1 Данные

`Processor.process_file` получает одно изменение: `result["utterances"] =
utterances` (список `{transcription, boundaries:(start,end), words?:[{text,
start,end}], speaker?}`), чтобы API не парсил `.txt` из `output_dir`. Файлы
на диске API больше не запрашивает: `output_formats=[]` (процессор это
допускает — `total_formats = max(len(output_formats), 1)`, цикл записи пуст).

Сборка ответов — чистый модуль `src/services/transcript_formats.py`
(без FastAPI, юнит-тестируемый):

- `build_json(utts) -> {"text", "usage": {"type": "duration", "seconds": int}}`
- `build_verbose(utts, duration, language, granularities)`:
  `{"task": "transcribe", "language", "duration", "text", "segments": [...],
  "words": [...]?}`; сегмент — поля OpenAI: `id, seek(0), start, end, text,
  tokens([]), temperature(0.0), avg_logprob(0.0), compression_ratio(0.0),
  no_speech_prob(0.0)`; при `diarize` добавляется `speaker`. `words` только при
  `word` в granularities и только если бэкенд их дал (иначе `[]`).
- `build_diarized(utts, duration) -> {"task": "transcribe", "duration", "text",
  "segments": [{"id","type":"transcript.text.segment","start","end","speaker","text"}]}`;
  `speaker` — как в диаризаторе (`SPEAKER_00` → OpenAI отдаёт буквы `A`, `B`…;
  делаем то же: порядковая буква по первому появлению).
- `text` — полный текст, строки через пробел; `srt`/`vtt` — существующие
  `formatters.generate_srt/generate_vtt` с настройками субтитров по умолчанию
  (`subtitle_options` из настроек приложения).
- `usage.seconds` — `ceil(media_duration)`.

`Content-Type`: `application/json` для json/verbose/diarized, `text/plain;
charset=utf-8` для `text`, `application/x-subrip` для `srt`, `text/vtt` для `vtt`.

### 3.2 Выполнение

- Один глобальный `processing_semaphore` (как сейчас, `MAX_CONCURRENT`), ожидание
  очереди внутри запроса. Rate limit slowapi остаётся (`10/minute` на
  транскрибацию, настраивается как сейчас).
- Файл пишется во временную директорию запроса (`tempfile.mkdtemp` под
  `UPLOAD_DIR`), удаляется в `finally`/`BackgroundTask` после ответа — включая
  стрим и ошибки.
- Модель: глобальный `model_loader`; `asr_backend/onnx_provider/model`
  отличные от активных → `acquire_request_model_loader` (как сейчас).
- Таймаут запроса не вводим (OpenAI тоже нет); документируем, что для длинных
  файлов клиент должен ставить свой `timeout` или использовать `stream=true`.

### 3.3 Стриминг (`stream=true`)

`text/event-stream`. Процессор отдаёт реплики только в конце, поэтому:

1. Пока идёт обработка — раз в 5 с (или на каждом progress-колбэке, но не чаще
   1/с) отправляется SSE-комментарий `: progress <stage> <percent>` — не событие,
   клиенты SDK его игнорируют, но соединение и прокси живы.
2. По готовности — `data: {"type":"transcript.text.delta","delta":"<текст реплики> "}`
   на каждую реплику, затем `data: {"type":"transcript.text.done","text":"<полный
   текст>","usage":{...}}`.
3. Ошибка после начала стрима → `data: {"type":"error","error":{...}}` и закрытие.

Честно документируем: дельты приходят пакетом после распознавания, а не
по мере него. `diarized_json` + `stream` → дельты содержат поле `speaker`
(расширение) — или проще: `stream` разрешён только для `json` и
`verbose_json`; для остальных `400`. **Решение: только `json`/`verbose_json`.**

## 4. Ошибки

Единый конверт OpenAI для всех кодов (`HTTPException`, `RequestValidationError`,
404, 405, 413, 415, 429, 500):

```json
{"error": {"message": "...", "type": "invalid_request_error", "param": "file", "code": "unsupported_file"}}
```

Типы: `invalid_request_error` (400/404/413/415/422), `authentication_error`
(401), `rate_limit_error` (429), `server_error` (500/503). Тексты сообщений —
по-английски (это машинный контракт; GUI не показывает их пользователю).

Коды: `invalid_api_key`, `model_not_found`, `unsupported_file`,
`file_too_large`, `unsupported_response_format`, `stream_not_supported`,
`translation_not_supported`, `diarization_unavailable` (503, если бэкенд
диаризации не собран/нет токена), `model_not_loaded` (503).

## 5. `GET /v1/models`

```json
{"object": "list", "data": [
  {"id": "v3_e2e_rnnt", "object": "model", "created": 0, "owned_by": "gigaam",
   "description": "GigaAM v3 e2e RNNT (current)", "default": true,
   "aliases": ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe", "gigaam"]},
  ...]}
```

Плюс расширение `"gigaam": {"backends": [...], "onnx_providers": [...],
"active": model_loader.diagnostics()}` на уровне ответа списка — замена
удалённого `/api/v1/asr/options`. `created` — 0 (нет даты), поле обязательно
по схеме OpenAI.

## 6. Структура кода

- `api.py` — переписывается: приложение, lifespan (загрузка модели, ключи,
  CORS, rate limit), обработчики ошибок, три маршрута. Цель ≤ 600 строк.
  Удаляются: `tasks_storage`, `process_transcription`, cleanup-loop,
  `UploadResponse/TaskStatus/...`, batch, download, `validated_task_id`,
  `TASK_ID_RE`, meta.json.
- `src/services/transcript_formats.py` — новый, чистые функции §3.1.
- `src/services/openai_errors.py` (или внутри `api.py`, если < 60 строк) —
  `OpenAIError(HTTPException)` + обработчики.
- `src/core/processor.py` — `result["utterances"]`.
- `src/services/task_store.py` — остаётся: его использует `web/web_app.py`.

## 7. Потребители и документация (всё обновляется в этой же работе)

| Место | Что сделать |
|---|---|
| `macos/GigaAMLiquid/.../main.swift` `apiExample`, `Localization.swift` | пример `curl` на `/v1/audio/transcriptions` с `Authorization: Bearer`; упомянуть SDK OpenAI |
| `src/gui/support_surfaces_mixin.py` (вкладка API, ru/en) | те же три строки: transcriptions, models, health |
| `desktop/ui/app.js`, `desktop/ui/index.html` (прототип) | примеры python/curl/js |
| `postman/GigaAM_API.postman_collection.json`, `postman/README.md` | коллекция заново: health, models, transcriptions × форматы, stream, ошибки; README короткий |
| `docs/API.md` | **единый** справочник: быстрый старт (curl, python `openai`, js `openai`), таблица параметров, форматы ответов с примерами, стрим, ошибки, расширения, отличия от OpenAI. Заменяет `API_GUIDE.md`, `API_QUICKSTART.md`, `API_SCHEMA.md`, `API_ENDPOINTS_MAP.txt` |
| `docs/POSTMAN_*.md`, `docs/START_WITH_POSTMAN.md`, `docs/POSTMAN_DOCUMENTATION_REPORT.md` | удалить (описывают удалённое API); одна секция «Postman» в `docs/API.md` |
| `docs/START_HERE.md`, `README.md` §API, `README_EN.md` §API | ссылки и примеры на новое API |
| `docs/CHANGELOG.md` | запись «Breaking: REST API заменён на OpenAI-совместимый» |
| `tests/test_release_hardening.py::test_tauri_api_examples_match_authenticated_v1_contract` | переписать под `/v1/audio/transcriptions` + `Bearer` |

## 8. Тесты

Стиль как в `tests/test_api_*.py`: `TestClient`, поддельный процессор
(monkeypatch `transcription_service.build_processor` → объект, чей
`process_file` пишет ничего и возвращает `{"success": True, "media_duration":
12.4, "utterances": [...], "total_time": 0.1, "diarization": {...}}`), без
модели и железа. Старые `test_api_integration.py`, `test_api_progress.py`
удаляются; `test_api_security.py` переписывается (ключи, размер, path
traversal, constant-time — актуальны) → `tests/test_api_openai.py` и
`tests/test_transcript_formats.py`:

- форматы: `json`, `text`, `srt`, `vtt`, `verbose_json` (segment/word),
  `diarized_json` (буквы спикеров, авто-`diarize`);
- алиасы модели, `model_not_found`, `/v1/models` содержит расширение `gigaam`;
- Bearer и X-API-Key, 401 в OpenAI-конверте, 422 валидации в конверте,
  404 старого пути `/api/v1/transcribe` в конверте;
- `stream=true`: последовательность событий delta…done, `400` для `srt`;
- `translations` → 400 `translation_not_supported`;
- временная директория удалена после ответа (и после ошибки процессора);
- `known_speaker_names[]` → 400; `num_speakers` + `sortformer` → 400.
- `test_transcript_formats.py`: чистые функции, включая пустой список реплик и
  реплики без `words`.

Обязательная проверка живьём (контроллер): запустить `api.py` с реальной
моделью, прогнать `scratchpad/hl/speech.wav` через `openai` SDK
(`client.audio.transcriptions.create(model="whisper-1", file=...)`) и `curl`
с `response_format=srt` и `stream=true`.

## 9. Риски

- Клиенты, ходившие на `/api/v1/*`, ломаются — по решению пользователя;
  CHANGELOG и релиз-ноты помечают breaking change, мажорную версию не
  поднимаем (пользовательские приложения — GUI — не зависят от REST).
- Долгие синхронные запросы через прокси (nginx `proxy_read_timeout`) —
  документировать `stream=true` как keep-alive.
- `words` есть не у всех бэкендов — `verbose_json` с `word` отдаёт `[]`,
  документировать.
