# GigaAM Transcriber 2.5.1

Патч-релиз: локальный MCP без постоянно загруженной ASR-модели и исправление очистки памяти общего ядра.

## Исправлено

- **Локальный MCP загружает модель по требованию.** Подключение `gigaam mcp` больше не загружает ASR-веса. Каждый `transcribe` создаёт отдельную модель и освобождает её после обработки, в том числе при ошибке. Сам MCP-процесс остаётся подключённым.
- **Исправлена выгрузка модели.** Ссылка `ModelLoader.model` освобождается до очистки backend-кэша, чтобы буферный пул MLX не удерживал память уже выгруженной модели. Исправление общего ядра входит также в Liquid и обычные настольные сборки.
- **Исправлен запуск MCP из установки TUI.** Добавлена зависимость `yt-dlp`, отсутствие которой приводило к ошибке импорта при запуске сервера. Установщик проверяет MCP entry point без загрузки весов.
- **Сохранена подпись macOS-приложения после распознавания.** Статистика обработки записывается в пользовательский каталог конфигурации, а не внутрь `.app`; явно заданный `STATS_FILE` сохраняет приоритет.

## Документация

- Добавлен [гайд локальной интеграции](https://github.com/dubr1k/GigaAMGUI/blob/v2.5.1/docs/LOCAL_HARNESSES.md) для Claude Code, Codex, OMP, Pi, OpenCode и Hermes.
- Обновлены скиллы и описание статуса: `asr.loader_loaded=false` при `asr.error=null` нормально для локального stdio и не запрещает распознавание.

## Обновление

- TUI / локальный MCP: `gigaam --update`, затем переподключите MCP или перезапустите harness.
- Liquid и обычный GUI: замените приложение сборкой 2.5.1; обновление TUI не меняет Python-ядро внутри ранее скачанного `.app`.
- Постоянная загрузка модели в HTTP/REST, веб-панели и GUI не менялась. Выгрузка после каждого задания относится только к локальному MCP stdio.

---

## English

Patch release: on-demand ASR loading for local MCP and corrected model-memory cleanup in the shared core.

### Fixed

- Connecting to `gigaam mcp` no longer loads ASR weights. Each `transcribe` owns a model and releases it after processing, including failures; the MCP process remains connected.
- The shared model loader releases its model reference before clearing the backend cache, preventing MLX's buffer pool from retaining a model's freed buffers. Liquid and regular desktop builds include this core fix.
- The TUI environment now installs `yt-dlp`, fixing an import failure when starting MCP. The installer checks the MCP entry point without loading model weights.
- Processing statistics now default to the persistent user configuration directory instead of the executable's directory, so transcription does not modify the signed macOS application bundle. An explicit `STATS_FILE` override still takes precedence.

### Documentation and updating

- Added a [local harness integration guide](https://github.com/dubr1k/GigaAMGUI/blob/v2.5.1/docs/LOCAL_HARNESSES.md) and updated both agent skills.
- `asr.loader_loaded=false` with `asr.error=null` is expected for local stdio; call `transcribe` directly.
- Update TUI / local MCP with `gigaam --update`, then reconnect MCP or restart the harness. Replace downloaded desktop applications with the 2.5.1 builds to update their embedded Python core.
- HTTP/REST, web and GUI model lifecycles are unchanged. Per-request loading and unloading apply only to local MCP stdio.
