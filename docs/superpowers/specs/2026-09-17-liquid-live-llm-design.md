# GigaAMLiquid: Live-транскрибация и LLM (релиз 2.1.0)

Дата: 2026-09-17. Статус: одобрено пользователем в обсуждении, реализация по
плану `docs/superpowers/plans/2026-09-17-liquid-live-llm.md`.

## Цель

Страницы Live и LLM в нативном macOS-клиенте `GigaAMLiquid` сейчас макеты:
кнопки выключены через `unavailableButton`, подпись «Live и LLM пока не
подключены». Обе функции целиком работают в PyQt-клиенте на общем стеке
`src/live` и `src/services/llm_service.py`. Задача: подключить обе страницы к
тому же Python-стеку через worker-протокол, не дублируя логику ASR, микшера,
диаризации и экспорта. Оба блока выходят одним релизом 2.1.0.

## Решения, принятые пользователем

1. Live и LLM выходят вместе, одним релизом.
2. Захват звука живёт в Swift (AVAudioEngine + ScreenCaptureKit), а не в
   Python-worker. Worker получает готовый PCM.
3. Транспорт PCM: base64 внутри тех же JSON-строк на stdin worker'а, 16 кГц
   моно int16, чанки по 100 мс (вариант 1 из обсуждения).

## Архитектура

```
GigaAMLiquid (Swift)                       GigaAMTranscriber --native-worker (Python)
─────────────────────                      ────────────────────────────────────────
LiveCapture.swift                          src/tui_worker.py  (маршрутизатор команд)
  AVAudioEngine  ─┐                          │
  SCStream audio ─┴─ AVAudioConverter ──►    ├─ src/services/live_worker_service.py
     16 kHz mono int16, 100 ms               │     LiveSession (src/live/session.py, без изменений)
                                             │       └─ PushCaptureAdapter (src/live/capture/push.py, новый)
LiveSessionJob.swift  ◄── JSONL stdout ──    │       └─ LiveAsrScheduler + ModelLoader
   (по образцу NativeTranscriptionJob)       │
                                             └─ src/services/llm_worker_service.py
LLMJob.swift          ◄── JSONL stdout ──          llm_service.run_provider (без изменений)
```

Принцип: Swift владеет железом и разрешениями, Python владеет всей
обработкой. Companion (`GigaAMTranscriber.app`) не запрашивает ни микрофон, ни
запись экрана.

## Протокол worker (расширение JSONL)

Все команды идут в stdin по одной JSON-строке; события в stdout. Существующие
`ping`, `start`, `cancel`, `llm_start` сохраняют поведение.

### Live: команды

| Команда | Поля | Ответ / ошибка |
|---|---|---|
| `live_start` | `session_root` (папка сессий), `sources` (`["mic"]`, `["system"]`, оба), `sample_rate` (16000), `diarization_mode` (`off`/`live_estimate`/`after_stop`), `diarization_backend`, `record_mic`, `record_system`, `exports` (объект с полями `ExportSelection`), `backend`, `model`, `onnx_provider` | `live_status` со `state: recording` или `error`, если сессия уже идёт или идёт батч |
| `live_audio` | `source`, `seq` (монотонный с 0 на источник), `sample_offset`, `timestamp_ns`, `pcm` (base64 int16 LE mono) | без ответа; пропуск `seq` даёт событие `live_capture_event` с `kind: discontinuity` |
| `live_capture_event` (от Swift) | `source`, `kind` (`permission_denied`, `device_removed`, `overflow`), `detail` | без ответа; отражается в `LiveSession._on_event` |
| `live_pause`, `live_resume` | нет | `live_status` |
| `live_stop` | нет | `live_stopped` с `session_dir`, `saved_files`, `recordings` |
| `live_ask` | `question`, `settings` (как у `llm_start`) | поток `live_answer_chunk`, затем `live_answer` |
| `live_ask_cancel` | нет | `live_answer` со `status: cancelled` |

### Live: события

| Событие | Поля |
|---|---|
| `live_status` | `state`, `active_sources`, `failed_sources` |
| `live_partial` | `event_id`, `revision`, `source`, `sample_start`, `sample_end`, `text` |
| `live_final` | те же плюс `speaker`, `paragraph_break_after` |
| `live_capture_event` | `source`, `kind`, `detail` |
| `live_answer_chunk` | `turn_id`, `text` |
| `live_answer` | `turn_id`, `status` (`complete`/`error`/`cancelled`), `text` |
| `live_stopped` | `session_dir`, `saved_files`, `recordings`, `message` |
| `log` | `message` (диагностика LiveSession) |

### LLM: расширение `llm_start`

`llm_start` дополнительно принимает `text` (строка) как альтернативу `files`;
`settings` те же, что собирает PyQt в `_collect_llm_settings`: `provider`,
`api_url`, `api_key`, `model`, `temperature`, `*_path`, `*_args`. Новое событие
`llm_chunk` с полем `text` для потокового вывода провайдера API. Новая команда
`llm_cancel`, ответ `llm_completed` со `success: false`, `cancelled: true`.

## Python

### `src/live/capture/push.py` (новый)

`PushCaptureAdapter(source, sample_rate, channels=1)` реализует
`CaptureAdapter`: `start` запоминает колбэки, `push(seq, sample_offset,
timestamp_ns, frames)` строит `PcmChunk` и вызывает `on_chunk`; `event(kind,
detail)` вызывает `on_event`. Пропуск `seq` порождает `discontinuity` и не
роняет сессию. `pause` глушит входящие чанки, `stop` закрывает адаптер, после
чего `push` игнорируется. `devices()` возвращает одно виртуальное устройство.

### `src/services/live_worker_service.py` (новый)

`LiveWorkerService(emit, model_loader_factory)`:
- `start(command)` валидирует поля, создаёт адаптеры, `LiveSession` с
  `LiveAsrScheduler` и `_LiveModelBackend` (вынести из `live_mixin` в
  `src/live/asr_backend.py`, чтобы не импортировать `src.gui`), подписывается
  и транслирует события в JSONL.
- `audio(command)` декодирует base64 в float32, передаёт адаптеру.
- `pause/resume/stop`, `ask/ask_cancel` через `LiveSession.begin_conversation`
  и `llm_service.run_provider` в daemon-потоке с `cancel_check`.
- Одна сессия на worker; `start` при активной сессии или батче отвечает
  `error`.

### `src/services/llm_worker_service.py` (новый)

Вынести `_start_llm`/`_run_llm` из `TuiWorker` сюда, добавить `text`,
`llm_chunk` и `llm_cancel`. Worker становится тонким маршрутизатором.

### `src/tui_worker.py`

Добавить ветки `live_*`, `llm_cancel`; батч-`start` отклоняется при активной
live-сессии тем же сообщением «Processing is already running».

## Swift

### `LiveCapture.swift` (новый)

- `MicrophoneCapture`: `AVAudioEngine` + `inputNode`, выбор устройства через
  `AVCaptureDevice` uid → CoreAudio device id, конверсия в 16 кГц моно int16
  через `AVAudioConverter`, чанки 100 мс, монотонный `sample_offset`.
- `SystemAudioCapture`: `SCStream` с `capturesAudio = true`, без видео (окно
  1×1 px, `minimumFrameInterval` большой), `SCStreamOutput` для `.audio`,
  та же конверсия. Требует macOS 13, что совпадает с `LSMinimumSystemVersion`.
- Разрешения: микрофон через `AVCaptureDevice.requestAccess(for: .audio)`,
  системный звук через `CGRequestScreenCaptureAccess()`. Отказ превращается в
  `live_capture_event` `permission_denied` и подсказку открыть «Системные
  настройки → Конфиденциальность».
- Уровень сигнала (RMS) отдаётся в UI для индикатора.

### `LiveSessionJob.swift` (новый)

По образцу `NativeTranscriptionJob`: один процесс worker на сессию, очередь
`DispatchQueue`, `LineReader` для stdout/stderr, ровно одно терминальное
событие. Пишет команды и звук в stdin, разбирает события в enum
`LiveSessionEvent`. `terminate()` синхронный для выхода из приложения.

### `LLMJob.swift` (новый)

Аналогично, для `llm_start`/`llm_cancel`, события `LLMJobEvent`
(`chunk`, `completed(saved, text)`, `failed`).

### `main.swift`

- Страница Live: список устройств микрофона (AVCaptureDevice), тумблер
  системного звука, режим диаризации, экспорт (те же семь форматов, что в
  PyQt), папка сессий, кнопки запись/пауза/стоп, таймер, индикатор уровня,
  транскрипт с заменой partial на final, поле вопроса ассистенту с ответом.
- Страница LLM: провайдер (`API`, `Claude Code`, `Codex`, `OpenCode`, `Pi`,
  `Other`), поля по провайдеру, ключ в Keychain через `SecureStore`, источник
  из файла или вставленного текста, шаблоны, стриминг результата,
  «Копировать», «Сохранить .md».
- Настройки → LLM: те же поля, что на странице, без дублирования логики.
- Убрать все `unavailableButton`/`unavailableIcon` на этих страницах и
  подпись «Live и LLM пока не подключены» в «О приложении».
- Плавающий оверлей PyQt не переносится.

### Секреты

`api_key` LLM хранится только в Keychain (`SecureStore`, ключ `llmApiKey`),
в UserDefaults не пишется. В stdin worker'а ключ уходит в `settings` команды;
`safeText` в job'ах маскирует его в логах, как уже сделано для `HF_TOKEN`.

## Обработка ошибок

- Worker упал или закрыл stdout без `live_stopped`: job шлёт `failed`, UI
  показывает причину и оставляет уже полученные финальные фрагменты.
- `permission_denied` для одного источника при двух активных: сессия
  продолжается на оставшемся, UI показывает предупреждение.
- Переполнение очереди stdin (worker не успевает): Swift держит буфер до 5 с
  на источник, дальше отбрасывает чанки и шлёт `overflow`.
- Батч и Live взаимно исключают друг друга в одном worker'е; UI блокирует
  запуск второго и объясняет причиной.

## Тестирование

Python (`tests/`):
- `test_live_capture_push.py`: чанки, seq-разрывы, pause/stop.
- `test_live_worker_service.py`: полный цикл `live_start` → `live_audio` →
  `live_stop` с `FakeScheduler` из `test_live_session.py`, события в JSONL,
  отказ второй сессии, `live_ask` с поддельным провайдером.
- `test_llm_worker_service.py`: `text` вместо `files`, `llm_chunk`, отмена.
- `test_tui_worker.py`: маршрутизация новых команд.

Swift (`tests/test_macos_swift_packaging.py`): структурные проверки наличия
`LiveCapture.swift`, `LiveSessionJob.swift`, `LLMJob.swift`, отсутствия
`unavailableButton` в `buildLive`/`buildLLM`, Keychain для `llmApiKey`,
`CGRequestScreenCaptureAccess`.

CI: `scripts/native_worker_smoke.py` получает режим `--live`: читает WAV,
режет на чанки 100 мс, шлёт `live_start`/`live_audio`/`live_stop`, ждёт
`live_final` и `live_stopped`. Гейт в `build-macos-swift`.

Ручная проверка перед релизом: реальный микрофон и системный звук на этой
машине, вопрос ассистенту через API-провайдер, LLM-страница с файлом и
вставленным текстом.

## Вне объёма

- Плавающий оверлей Live.
- Записи 48 кГц (сессии Liquid пишутся в 16 кГц).
- История сессий в разделе «Журнал».
- Windows/Linux для Liquid.

## Релиз

Версия 2.1.0. Bump в девяти местах по `docs/CHANGELOG.md` 2.0.5, release
notes `docs/RELEASE_NOTES_2.1.0.md`, тег `v2.1.0`.
