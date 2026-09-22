# REST API (совместим с OpenAI Audio API)

GigaAM v3 Transcriber поднимает HTTP-сервер (`api.py`, FastAPI), который
повторяет контракт OpenAI Audio API: `POST /v1/audio/transcriptions`,
`GET /v1/models`, ошибки в конверте OpenAI. Любой клиент, написанный под
`openai.audio.transcriptions.create(...)` — официальные SDK, `curl`, плагины
Obsidian, n8n, Home Assistant, Open WebUI — работает с GigaAM, если поменять
`base_url` на `http://127.0.0.1:8000/v1` и ключ. Ключ создаётся при первом
запуске и печатается в консоль один раз (в `.api_keys` хранится только SHA-256
хэш). Запуск: `python api.py` или `uvicorn api:app --host 127.0.0.1 --port 8000`;
интерактивная OpenAPI-документация — `http://127.0.0.1:8000/docs`. Тот же
сервер отдаёт `/mcp` — MCP-сервер для ИИ-агентов с тем же ключом и лимитами,
см. [MCP.md](MCP.md).

Содержание:

- [Быстрый старт](#быстрый-старт)
- [Авторизация](#авторизация)
- [POST /v1/audio/transcriptions](#post-v1audiotranscriptions)
- [Стриминг (stream=true)](#стриминг-streamtrue)
- [GET /v1/models и GET /v1/models/{id}](#get-v1models-и-get-v1modelsid)
- [Ошибки](#ошибки)
- [Отличия от OpenAI](#отличия-от-openai)
- [Расширения GigaAM](#расширения-gigaam)
- [Postman](#postman)
- [Переменные окружения](#переменные-окружения)

## Быстрый старт

**curl** (ответ `json`):

```bash
curl http://127.0.0.1:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $GIGAAM_API_KEY" \
  -F "file=@speech.wav" \
  -F "model=whisper-1"
```

```json
{"text": "Привет, как дела?", "usage": {"type": "duration", "seconds": 4}}
```

**Python** (`pip install openai`):

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="gam_...")

with open("speech.wav", "rb") as audio:
    result = client.audio.transcriptions.create(model="whisper-1", file=audio)
print(result.text)
```

**Node.js** (`npm install openai`):

```js
import fs from "node:fs";
import OpenAI from "openai";

const client = new OpenAI({ baseURL: "http://127.0.0.1:8000/v1", apiKey: "gam_..." });

const result = await client.audio.transcriptions.create({
  file: fs.createReadStream("speech.wav"),
  model: "whisper-1",
});
console.log(result.text);
```

## Авторизация

Все маршруты `/v1/*` требуют ключ. Принимаются два заголовка:

- `Authorization: Bearer <key>` — так шлют SDK OpenAI (регистр слова `Bearer`
  не важен);
- `X-API-Key: <key>` — для старых скриптов и примеров в GUI. Если есть оба
  заголовка, `Authorization` проверяется первым; `X-API-Key` используется,
  когда `Authorization` не начинается с `Bearer`.

Ключи лежат в файле `.api_keys` (путь меняется переменной `API_KEYS_FILE`),
по одному SHA-256 хэшу в строке, права `0600`. Если файла нет, при старте
сервер создаёт ключ вида `gam_<32 hex>`, печатает его в консоль и сохраняет
только хэш — восстановить ключ из файла нельзя. Чтобы добавить ключ вручную:

```bash
printf 'gam_my_second_key' | shasum -a 256 | cut -d' ' -f1 >> .api_keys
```

Строки без хэша (ключ открытым текстом) при следующем старте мигрируют в хэши.
Сравнение хэшей — за постоянное время.

`GET /health`, `GET /`, `/docs`, `/openapi.json` ключа не требуют.

Без ключа или с неверным ключом — `401`:

```json
{
  "error": {
    "message": "Incorrect API key provided.",
    "type": "authentication_error",
    "param": null,
    "code": "invalid_api_key"
  }
}
```

## POST /v1/audio/transcriptions

`multipart/form-data`, один файл за запрос, ответ синхронный (или SSE при
`stream=true`). Лимит: `RATE_LIMIT_UPLOAD` (10 запросов в минуту) с одного IP (см. [Ошибки](#ошибки)).

| Поле | Тип | Поведение |
|---|---|---|
| `file` | file, обязательно | Аудио/видео: `mp3 wav m4a aac mp4 avi mov mkv webm flac ogg wma qta 3gp`. Другое расширение → `400 unsupported_file`; больше `MAX_FILE_SIZE` (2 ГБ по умолчанию) → `413 file_too_large`. Имя файла проходит защиту от path traversal. |
| `model` | str, обязательно | Id из `GET /v1/models` (`v3_e2e_rnnt`, `multilingual_ctc`, `multilingual_large_ctc`) **или алиас** `whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `gigaam` → `v3_e2e_rnnt`. Неизвестное имя → `404 model_not_found`. |
| `language` | str | Принимается; на распознавание не влияет (модель сама определяет язык, `v3_e2e_rnnt` — только русский). Значение возвращается как есть в `verbose_json.language`; без него — `ru`. По ISO-списку не проверяется. |
| `prompt` | str | Принимается, игнорируется. |
| `response_format` | enum | `json` (по умолчанию), `text`, `srt`, `vtt`, `verbose_json`, `diarized_json`. Другое → `400 unsupported_response_format`. |
| `temperature` | float | Принимается, игнорируется. |
| `stream` | bool | `true`/`1`/`yes`/`on` → SSE (см. [Стриминг](#стриминг-streamtrue)). Только с `json` и `verbose_json`; иначе `400 stream_not_supported`. |
| `timestamp_granularities[]` | list | `segment` (по умолчанию) и/или `word`. Учитывается только в `verbose_json`. Другое значение → `400 unsupported_parameter`. |
| `include[]` | list | Принимается, игнорируется (logprobs нет). |
| `chunking_strategy` | str | Принимается, игнорируется — GigaAM всегда режет по VAD. |
| `known_speaker_names[]`, `known_speaker_references[]` | list | Не поддерживаются: непустой список → `400 unsupported_parameter`. |
| **`diarize`** | bool, расширение | Включить диаризацию (кто говорит). При `response_format=diarized_json` включается автоматически. |
| **`diarization_backend`** | str, расширение | `pyannote` (по умолчанию), `sortformer` (алиас `nvidia`), `onnx`. `pyannote` требует `HF_TOKEN` на сервере, иначе `503 diarization_unavailable`. Неизвестное → `400 unsupported_parameter`. |
| **`num_speakers`** | int ≥ 1, расширение | Число говорящих для `pyannote`/`onnx`. Вместе с `sortformer` → `400 unsupported_parameter`. |
| **`asr_backend`** | str, расширение | `auto` \| `pytorch` \| `onnx` \| `mlx`. По умолчанию — движок сервера. Другой движок/модель загружаются отдельно на время запроса и выгружаются после. Неизвестное → `400 unsupported_parameter`. |
| **`onnx_provider`** | str, расширение | `auto` \| `cpu` \| `cuda` \| `tensorrt` \| `coreml` \| `directml` (для `asr_backend=onnx`). |
| **`audio_preprocessing`** | str, расширение | `off` \| `auto` \| `light` \| `denoise` (алиас `deepfilter`). По умолчанию — `AUDIO_PREPROCESSING_MODE` сервера. |

Параметры-расширения удобно передавать из SDK через `extra_body`, см.
[Расширения GigaAM](#расширения-gigaam). В OpenAPI (`/docs`) они помечены как
«GigaAM extension».

### Ответы по `response_format`

Примеры ниже — для одного файла с двумя репликами: «Привет,» (0.0–1.5 с)
и «как дела?» (1.5–3.25 с).

`json` — `Content-Type: application/json`. `usage.seconds` — длительность
файла, округлённая вверх:

```json
{"text": "Привет, как дела?", "usage": {"type": "duration", "seconds": 4}}
```

`text` — `text/plain; charset=utf-8`, реплики через пробел:

```
Привет, как дела?
```

`srt` — `application/x-subrip`; `vtt` — `text/vtt`. Собираются теми же
форматтерами, что и в GUI/CLI (настройки субтитров по умолчанию). При
включённой диаризации строки получают метку говорящего из диаризатора
(`SPEAKER_00: …` в SRT, `<v SPEAKER_00>` в VTT):

```
1
00:00:00,000 --> 00:00:01,500
Привет,

2
00:00:01,500 --> 00:00:03,250
как дела?
```

```
WEBVTT

00:00:00.000 --> 00:00:01.500
Привет,

00:00:01.500 --> 00:00:03.250
как дела?
```

`verbose_json` — поля сегментов как у OpenAI; `seek`, `tokens`,
`temperature`, `avg_logprob`, `compression_ratio`, `no_speech_prob` всегда
нулевые/пустые (GigaAM их не считает). `words` появляется только при
`timestamp_granularities[]=word` и содержит слова лишь для движков, которые
отдают пословные тайминги, иначе `[]`. При `diarize=true` в каждый сегмент
добавляется `speaker` (`A`, `B`, … по порядку появления):

```json
{
  "task": "transcribe",
  "language": "ru",
  "duration": 3.25,
  "text": "Привет, как дела?",
  "segments": [
    {"id": 0, "seek": 0, "start": 0.0, "end": 1.5, "text": "Привет,", "tokens": [],
     "temperature": 0.0, "avg_logprob": 0.0, "compression_ratio": 0.0, "no_speech_prob": 0.0},
    {"id": 1, "seek": 0, "start": 1.5, "end": 3.25, "text": "как дела?", "tokens": [],
     "temperature": 0.0, "avg_logprob": 0.0, "compression_ratio": 0.0, "no_speech_prob": 0.0}
  ],
  "words": [
    {"word": "как", "start": 1.5, "end": 2.0},
    {"word": "дела?", "start": 2.0, "end": 3.25}
  ],
  "usage": {"type": "duration", "seconds": 4}
}
```

`diarized_json` — диаризация включается автоматически; говорящие — буквы
`A`, `B`, … в порядке первого появления (без диаризации все сегменты — `A`):

```json
{
  "task": "transcribe",
  "duration": 3.25,
  "text": "Привет, как дела?",
  "segments": [
    {"id": 0, "type": "transcript.text.segment", "start": 0.0, "end": 1.5, "speaker": "A", "text": "Привет,"},
    {"id": 1, "type": "transcript.text.segment", "start": 1.5, "end": 3.25, "speaker": "B", "text": "как дела?"}
  ]
}
```

## Стриминг (stream=true)

`stream=true` даёт `text/event-stream` (заголовки `Cache-Control: no-cache`,
`X-Accel-Buffering: no`). Разрешён только для `json` и `verbose_json`.
Честно: GigaAM отдаёт текст после того, как файл распознан целиком, поэтому
дельты приходят пакетом в конце, а не по мере распознавания. Смысл стрима —
держать соединение живым за прокси и таймаутами клиента на длинных файлах.

Пока идёт обработка, сервер шлёт SSE-комментарии (строки с `:` — SDK их
игнорируют): `: progress <stage> <pct>` на каждое событие прогресса процессора
(`preparing`, `conversion`, `preprocessing`, `transcription`, `diarization`, `export`, `finalizing`) и
`: keepalive`, если событий не было 5 секунд. Затем по одному
`transcript.text.delta` на реплику — у всех дельт, кроме последней, пробел на
конце, так что конкатенация даёт полный текст, — и `transcript.text.done`:

```
: progress conversion 100%

: progress transcription 42%

: keepalive

: progress transcription 100%

data: {"type": "transcript.text.delta", "delta": "Привет, "}

data: {"type": "transcript.text.delta", "delta": "как дела?"}

data: {"type": "transcript.text.done", "text": "Привет, как дела?", "usage": {"type": "duration", "seconds": 4}}
```

При `response_format=verbose_json` событие `transcript.text.done` дополнительно
несёт поля ответа `verbose_json` (`task`, `language`, `duration`, `segments`;
`words` — при `timestamp_granularities[]=word`). Ошибка после начала стрима приходит последним событием со статусом
`200` (заголовки уже отправлены):

```
data: {"type": "error", "error": {"message": "Transcription failed on the server. See the server log.", "type": "server_error", "param": null, "code": "processing_failed"}}
```

SDK:

```python
with open("long-meeting.mp3", "rb") as audio:
    stream = client.audio.transcriptions.create(model="whisper-1", file=audio, stream=True)
    for event in stream:
        if event.type == "transcript.text.delta":
            print(event.delta, end="", flush=True)
        elif event.type == "transcript.text.done":
            print()
```

```bash
curl -N http://127.0.0.1:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $GIGAAM_API_KEY" \
  -F "file=@long-meeting.mp3" -F "model=whisper-1" -F "stream=true"
```

## GET /v1/models и GET /v1/models/{id}

`GET /v1/models` — список в формате OpenAI плюс объект `gigaam` — замена
удалённого справочника параметров движка: какие `asr_backend` реально
доступны на этой машине, список `onnx_provider` и активная конфигурация
загруженной модели (`diagnostics()` загрузчика: движок, устройство,
провайдер, причина отката …). Поле `created` всегда `0`.

```json
{
  "object": "list",
  "data": [
    {
      "id": "v3_e2e_rnnt",
      "object": "model",
      "created": 0,
      "owned_by": "gigaam",
      "description": "GigaAM v3 e2e RNNT (current)",
      "default": true,
      "aliases": ["gigaam", "gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"]
    },
    {
      "id": "multilingual_ctc",
      "object": "model",
      "created": 0,
      "owned_by": "gigaam",
      "description": "GigaAM Multilingual CTC (220M)",
      "default": false,
      "aliases": []
    },
    {
      "id": "multilingual_large_ctc",
      "object": "model",
      "created": 0,
      "owned_by": "gigaam",
      "description": "GigaAM Multilingual Large CTC (600M)",
      "default": false,
      "aliases": []
    }
  ],
  "gigaam": {
    "backends": ["auto", "onnx", "mlx", "pytorch"],
    "onnx_providers": ["auto", "cpu", "cuda", "tensorrt", "coreml", "directml"],
    "active": {"requested_backend": "auto", "active_backend": "mlx", "model": "v3_e2e_rnnt", "device": "mps"}
  }
}
```

`GET /v1/models/{id}` принимает и id, и алиас (`/v1/models/whisper-1` →
объект `v3_e2e_rnnt`); неизвестный → `404 model_not_found`.

| Алиас | Модель |
|---|---|
| `whisper-1` | `v3_e2e_rnnt` |
| `gpt-4o-transcribe` | `v3_e2e_rnnt` |
| `gpt-4o-mini-transcribe` | `v3_e2e_rnnt` |
| `gigaam` | `v3_e2e_rnnt` |

Служебные маршруты без ключа: `GET /health` — `{"status", "version",
"model_loaded", "runtime": {"platform", "machine"}, "asr": {...}}`; `GET /` —
имя сервиса, версия и список маршрутов.

## Ошибки

Все ошибки, включая 404 неизвестного пути, 405, 422 валидации и необработанные
исключения, приходят в конверте OpenAI:

```json
{"error": {"message": "...", "type": "invalid_request_error", "param": "file", "code": "unsupported_file"}}
```

`param` — имя поля формы, к которому относится ошибка, или `null`. Тексты
сообщений — на английском.

| HTTP | `type` | `code` | Когда |
|---|---|---|---|
| 400 | `invalid_request_error` | `unsupported_file` | расширение файла не из списка поддерживаемых |
| 400 | `invalid_request_error` | `unsupported_response_format` | `response_format` вне списка |
| 400 | `invalid_request_error` | `stream_not_supported` | `stream=true` с `text`/`srt`/`vtt`/`diarized_json` |
| 400 | `invalid_request_error` | `unsupported_parameter` | `known_speaker_*`, неизвестная гранулярность, `diarization_backend`, `asr_backend`/`onnx_provider`, `num_speakers` + `sortformer` |
| 400 | `invalid_request_error` | `translation_not_supported` | `POST /v1/audio/translations` |
| 401 | `authentication_error` | `invalid_api_key` | нет ключа или ключ неверный. Для `POST /v1/audio/*` отсутствие заголовка `Authorization`/`X-API-Key` отклоняется по заголовкам, до чтения тела |
| 404 | `invalid_request_error` | `model_not_found` | неизвестный `model` / `/v1/models/{id}` |
| 404 | `invalid_request_error` | `null` | неизвестный путь (в том числе старые маршруты) |
| 405 | `invalid_request_error` | `null` | неверный метод |
| 413 | `invalid_request_error` | `file_too_large` | файл больше `MAX_FILE_SIZE`; если `Content-Length` превышает `MAX_FILE_SIZE` + 1 МиБ, ответ приходит по заголовкам, до чтения тела |
| 422 | `invalid_request_error` | `null` | ошибка валидации формы: нет `file`/`model`, `num_speakers` < 1 …; `param` — имя поля |
| 429 | `rate_limit_error` | `rate_limit_exceeded` | больше `RATE_LIMIT_UPLOAD` (10 в минуту) запросов на транскрибацию с одного IP |
| 500 | `server_error` | `processing_failed` | конвертация/распознавание упали; подробности в журнале сервера |
| 500 | `server_error` | `internal_error` | необработанное исключение; при `API_DEBUG=true` в `message` добавляется текст исключения |
| 503 | `server_error` | `diarization_unavailable` | диаризация `pyannote` без `HF_TOKEN` на сервере |
| 503 | `server_error` | `model_not_loaded` | модель ASR не загружена |

Ответ `429` несёт заголовки `Retry-After` и `X-RateLimit-Limit` /
`X-RateLimit-Remaining` / `X-RateLimit-Reset` — по ним SDK OpenAI делает
повторы с backoff.

## Отличия от OpenAI

- Модель по умолчанию `v3_e2e_rnnt` распознаёт только русскую речь;
  `whisper-1` и другие алиасы указывают на неё. Для других языков —
  `multilingual_ctc` / `multilingual_large_ctc`.
- `language`, `prompt`, `temperature`, `include[]`, `chunking_strategy`
  принимаются и игнорируются.
- `known_speaker_names[]` / `known_speaker_references[]` отклоняются (`400`).
- `POST /v1/audio/translations` всегда отвечает `400 translation_not_supported`;
  `/v1/audio/speech` и Realtime API нет.
- В `verbose_json` `tokens`, `avg_logprob`, `compression_ratio`,
  `no_speech_prob` не вычисляются; `words` пуст для движков без пословных
  таймингов.
- `stream=true` разрешён только для `json`/`verbose_json`, дельты приходят
  после распознавания всего файла.
- `usage` — только `{"type": "duration", "seconds"}`, токены не считаются.
- Таймаута на запрос нет: длинный файл держит соединение столько, сколько
  идёт обработка. За прокси (nginx, Cloudflare) используйте `stream=true` —
  комментарии прогресса не дают соединению заснуть — или поднимайте таймауты
  клиента (`OpenAI(timeout=...)`).
- Лимит `RATE_LIMIT_UPLOAD` (10 запросов в минуту) на транскрибацию с одного
  IP; одновременно обрабатываются `MAX_CONCURRENT_TASKS` файлов, остальные
  ждут внутри запроса.
- Ключ можно передавать и заголовком `X-API-Key`.

## Расширения GigaAM

Дополнительные поля формы, которых нет у OpenAI (полное описание — в таблице
параметров выше): `diarize`, `diarization_backend`, `num_speakers`,
`asr_backend`, `onnx_provider`, `audio_preprocessing`. Из SDK они передаются
через `extra_body`:

```python
with open("meeting.wav", "rb") as audio:
    result = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio,
        response_format="verbose_json",
        extra_body={"diarize": True, "num_speakers": 2, "audio_preprocessing": "auto"},
    )
for segment in result.segments:
    print(segment.speaker, segment.text)  # speaker — расширение, есть только при diarize
```

```bash
curl http://127.0.0.1:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $GIGAAM_API_KEY" \
  -F "file=@meeting.wav" -F "model=whisper-1" \
  -F "response_format=diarized_json" -F "diarization_backend=sortformer" \
  -F "asr_backend=onnx" -F "onnx_provider=coreml"
```

`diarize=true` без `diarization_backend` использует `pyannote` — серверу нужен
`HF_TOKEN` с доступом к моделям pyannote. `sortformer` и `onnx` токена не
требуют, но `sortformer` не принимает `num_speakers`.

## Postman

Импортируйте `postman/GigaAM_API.postman_collection.json` (File → Import),
откройте переменные коллекции и задайте `baseUrl` (по умолчанию
`http://127.0.0.1:8000`) и `apiKey`. Авторизация `Bearer {{apiKey}}` задана на
уровне коллекции; в запросах транскрибации выберите файл в поле `file`.
Коллекция содержит `Health`, `Models`, `Model by alias`, транскрибацию во всех
форматах, стрим и два запроса с ожидаемыми ошибками. Подробнее —
`postman/README.md`.

## Переменные окружения

Читаются из `.env` в корне проекта (или окружения процесса) при старте `api.py`.

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `API_HOST` | `127.0.0.1` | Адрес, на котором слушает uvicorn при `python api.py`. |
| `API_PORT` | `8000` | Порт. |
| `API_WORKERS` | `2` | Читается, но `python api.py` запускает один процесс; несколько воркеров — `uvicorn api:app --workers N` (лимит запросов и семафор тогда действуют на каждый процесс отдельно). |
| `MAX_FILE_SIZE` | `2147483648` (2 ГБ) | Лимит размера загрузки в байтах; превышение → `413 file_too_large`. |
| `MAX_CONCURRENT_TASKS` | `3` | Сколько файлов обрабатывается одновременно; остальные запросы ждут семафор. |
| `RATE_LIMIT_UPLOAD` | `10/minute` | Лимит `POST /v1/audio/transcriptions` с одного IP в формате slowapi (`число/период`: `10/minute`, `100/hour`); превышение → `429 rate_limit_exceeded`. Неразборное значение (например, `abc`) останавливает сервер при старте, а не отключает лимит молча. |
| `CORS_ORIGINS` | пусто | Разрешённые origin через запятую; пусто — кросс-доменные запросы из браузера запрещены. |
| `UPLOAD_DIR` | `uploads` | Куда кладутся временные директории запросов `req_*` (удаляются после ответа). |
| `API_KEYS_FILE` | `.api_keys` | Файл с SHA-256 хэшами ключей (общий с `/mcp`). |
| `API_DEBUG` | `false` | `true` — текст необработанного исключения попадает в `message` ответа `500` (только для отладки). |
| `HF_TOKEN` | пусто | Токен Hugging Face для `diarization_backend=pyannote`. |
| `AUDIO_PREPROCESSING_MODE` | `auto` | Режим подготовки аудио по умолчанию (`off`/`auto`/`light`/`denoise`). |
| `GIGAAM_MCP_ALLOW_PATHS`, `GIGAAM_MCP_PATH_ROOT`, `GIGAAM_MCP_MAX_INLINE_MB` | см. [MCP.md](MCP.md) | Политика источника `path` и лимит base64 для `/mcp`. |
