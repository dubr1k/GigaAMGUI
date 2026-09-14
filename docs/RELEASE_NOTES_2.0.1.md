# GigaAM Transcriber 2.0.1

Исправляющий релиз для Desktop-интерфейсов и упаковки GigaAMLiquid.

## Исправления

- Liquid-архивы больше не содержат исходный код репозитория: публикуются только
  готовые `GigaAMLiquid.app`, companion runtime и необходимые модели.
- Перед запуском batch нативный macOS-клиент блокирует коллизии одинаковых имён
  файлов, которые могли привести к перезаписи результатов.
- PyQt очищает результаты предыдущего batch до подготовки нового запуска и не
  открывает устаревшие результаты после ошибки или отмены.
- Сохранённая тёмная тема PyQt применяется при следующем запуске и получает
  полноценную тёмную палитру.
- HF token перенесён в macOS Keychain; экспериментальная Tauri-оболочка больше
  не сохраняет его в `localStorage`.
- Загруженные по URL временные медиа удаляются при очистке списка и завершении
  работы приложения.
- В окне «О программе» отображается фактическая версия релиза; версии PyQt,
  AppKit, Tauri, npm и Cargo синхронизированы (`#51`).
- VAD больше не может молча отбросить длинный активный участок аудио: если после
  аплодисментов, музыки или шума остаётся нераспознанная активная дорожка,
  включается полное overlap-разбиение файла (`#52`).
- Исправлены примеры Tauri API: endpoint `/api/v1/transcribe`, актуальные query-
  параметры и обязательный заголовок `X-API-Key`.
- Добавлены lock-файлы npm/Cargo и CI-проверки для pull request: Ruff,
  `compileall`, регрессионные тесты, JavaScript, locked Cargo metadata и Swift
  build.

## Packaging

Архивы GigaAMLiquid дополнительно проверяются во время сборки: наличие `src/`
или `app.py` завершает release job ошибкой.

---

This patch release removes repository sources from GigaAMLiquid archives,
prevents output-name collisions and stale batch results, restores the saved
PyQt dark theme, protects the HF token, cleans downloaded media caches, shows
the real application version, prevents VAD from dropping long active audio
regions, fixes the Tauri API examples, adds npm/Cargo lockfiles, and introduces
pull-request CI checks.
