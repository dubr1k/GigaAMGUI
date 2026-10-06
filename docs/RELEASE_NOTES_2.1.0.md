# GigaAM Transcriber 2.1.0

Функциональный релиз нативного macOS-клиента GigaAMLiquid: страницы Live и
LLM больше не макеты, а работают через тот же Python-стек, что и PyQt-клиент.

## Live

- **Захват звука в самом приложении.** Микрофон через AVAudioEngine с выбором
  устройства, системный звук через ScreenCaptureKit (тумблер «Системный
  звук»). Клиент приводит звук к 16 кГц int16 и шлёт его companion-worker'у
  чанками по 100 мс; worker гоняет тот же `LiveSession`, что и PyQt.
- **Потоковая транскрипция.** Черновики (partial) заменяются финальными
  строками, таймер и индикатор уровня, пауза/продолжение, остановка с экспортом.
- **Диаризация.** Режимы «Выкл.», «Оценка вживую», «После остановки»; движки
  pyannote, onnx, sortformer. Ручного числа спикеров в live нет.
- **Экспорт.** TXT, таймкоды, диаризация, диаризация с таймкодами, Markdown,
  SRT, VTT и настройки субтитров; папка сессий настраивается на странице
  (по умолчанию `~/Documents/GigaAM/live`). Записи сессий в 16 кГц.
- **Вопросы ассистенту.** Поле вопроса под транскриптом, ответ стримится из
  выбранного LLM-провайдера с контекстом текущей записи.
- **Разрешения.** macOS спросит доступ к микрофону при первой записи и
  «Запись экрана и системного звука» при включённом системном звуке.
  Разрешения запрашивает только GigaAMLiquid; companion к железу не обращается.

## LLM

- Страница LLM и Настройки → LLM: провайдеры API (OpenAI-совместимый или
  Anthropic адрес), Claude Code, Codex, OpenCode, Pi и «Другое» (произвольная
  команда) — как в PyQt. Ключ API хранится в Связке ключей и маскируется в
  логах. Исходный текст из файла или вставленный, шаблоны или свой промпт,
  потоковый результат, «Копировать» и «Сохранить .md».

## Протокол worker

Новые команды `live_start`, `live_audio`, `live_capture_event`, `live_pause`,
`live_resume`, `live_stop`, `live_ask`, `live_ask_cancel`, `llm_cancel` и
события `live_status`, `live_partial`, `live_final`, `live_capture_event`,
`live_answer_chunk`, `live_answer`, `live_stopped`, `llm_chunk`. `llm_start`
принимает `text` вместо файлов. Батч, LLM и live взаимно исключают друг друга в
одном worker'е.

## Проверено

- Live на реальном микрофоне через релизный бандл: запись, partial → final,
  остановка, в папке сессии `transcript.txt`, `transcript.srt`, `mic.flac`.
- LLM-страница end-to-end с провайдером «Другое» (`/bin/echo`): промпт и
  транскрипт дошли до worker, ответ показан и сохранён.
- Батч-транскрибация через общий `WorkerProcess` на реальном файле.
- Live-протокол через `native_worker_smoke.py --live` на source-tree worker:
  два `live_final` за 3.4 с, `transcript.txt` сохранён. Тот же гейт добавлен в
  CI для замороженного companion.
- pytest (полный набор), ruff, `swift build -c release` без предупреждений.

## Внутреннее

- `LLMWorkerService` и `LiveWorkerService` вынесены из `TuiWorker`;
  `LazyModelBackend` перенесён из Qt-миксина в `src/live/asr_backend.py`.
- `WorkerProcess.swift` (процесс, `LineReader`, маскирование секретов) общий
  для `NativeTranscriptionJob`, `LiveSessionJob` и `LLMJob`.
- Версии PyQt, AppKit, Tauri, npm и Cargo синхронизированы на `2.1.0`.

---

GigaAMLiquid 2.1.0 turns the Live and LLM pages into working features. The
client captures the microphone (AVAudioEngine) and, optionally, system audio
(ScreenCaptureKit) itself and streams 16 kHz PCM to the companion worker, which
runs the same live pipeline as the PyQt client: streaming recognition with
partial/final lines, optional diarization, seven export formats and assistant
questions over the current recording. The LLM page and settings support the
project's six providers with the API key kept in the Keychain. macOS asks for
Microphone access on the first recording and for Screen & System Audio
Recording when system audio is enabled; the companion never requests
permissions. Session recordings are 16 kHz.
