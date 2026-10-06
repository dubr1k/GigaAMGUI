# GigaAM Transcriber 2.5.4

Патч-релиз: остановка LLM снимает все процессы CLI-провайдера; в десктоп-сборки вошло общее ядро TUI 2.1.x.

## Исправлено

- **Остановка LLM снимает все процессы CLI-провайдера.** Раньше отмена завершала только сам CLI (Claude Code, Codex и др.), а запущенные им дочерние процессы продолжали работать и расходовать ресурсы. Теперь останавливается всё дерево процессов этого вызова. Если завершение не подтвердилось, приложение сообщает об ошибке, а не об успешной отмене.
- **Intel-сборка macOS:** добавлен `psutil`, который теперь нужен LLM-сервису.

## Изменено

- В десктоп-сборки вошло общее Python-ядро TUI 2.1.x: версия протокола воркера и разрешение входных путей на стороне воркера. Рукопожатие необязательно, поэтому Liquid и остальные клиенты работают как раньше.

## Что проверить

- LLM с CLI-провайдером: запустить длинный запрос, нажать «Остановить» — в Мониторе активности не должно остаться процессов провайдера.
- Liquid и обычный GUI: распознавание файла и Live работают как в 2.5.3-3.

TUI обновляется отдельно: `gigaam --update` (текущая версия TUI — 2.1.1).

---

## English

Patch release: stopping an LLM run now ends every process of the CLI provider; desktop builds include the shared TUI 2.1.x core.

### Fixed

- **Stopping an LLM run ends the whole CLI process tree.** Cancellation used to terminate only the CLI itself (Claude Code, Codex, etc.) while the processes it had spawned kept running and consuming resources. The whole process tree of that call is now stopped. If termination cannot be confirmed, the app reports an error instead of a successful cancellation.
- **Intel macOS build:** adds `psutil`, which the LLM service now needs.

### Changed

- Desktop builds include the shared Python core of TUI 2.1.x: a worker protocol version and worker-side input path resolution. The handshake is optional, so Liquid and the other clients work as before.

### What to check

- LLM with a CLI provider: start a long request and press Stop — no provider processes should remain in Activity Monitor.
- Liquid and the classic GUI: file transcription and Live work as in 2.5.3-3.

The TUI updates separately with `gigaam --update` (current TUI version: 2.1.1).
