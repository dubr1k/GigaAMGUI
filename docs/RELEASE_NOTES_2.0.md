# GigaAM Transcriber 2.0

Версия 2.0 обновляет Desktop-интерфейс и впервые добавляет отдельный нативный
клиент для macOS на Swift/AppKit.

## Русский

### Нативный Swift-клиент для macOS

- Добавлен `GigaAMLiquid` для Apple Silicon и macOS 13+.
- Клиент принимает локальные медиафайлы и URL, показывает ход обработки,
  поддерживает очередь и остановку после текущего файла, а также сохраняет TXT
  и SRT.
- Светлая/тёмная тема и русский/английский язык сохраняются между запусками.
- GitHub Actions собирает Swift package в `.app`, проверяет архитектуру и
  подпись, а затем публикует архив `GigaAMLiquid-macos-arm64-v2.0.zip`.
- Для полностью автономной работы также публикуется
  `GigaAMLiquid-macos-arm64-offline-v2.0.zip` со встроенным frozen runtime и
  моделями. Установка Python и скачивание моделей для него не требуются.
- Оба Liquid-архива содержат только готовые приложения и необходимые runtime-
  файлы; исходный код репозитория в релизные ZIP не включается.

Swift-клиент использует готовое frozen companion-приложение для Python worker.
Обычный архив скачивает модели при первом использовании, offline-архив уже
содержит их. Приложение подписано ad-hoc и не нотарифицировано.

### Новый Desktop UI

- Переработаны экраны обработки, Live, LLM, настроек и вспомогательные окна.
- Добавлены адаптивные карточки, обновлённые светлая и тёмная темы, улучшенная
  локализация и более компактная компоновка.
- Live-экран получил три панели, таймер записи, отдельные кнопки управления и
  более ясное отображение текущего состояния.
- Реальная загрузка медиа по URL и транскрибация подключены к существующему
  Python worker с обработкой прогресса, результатов, экспорта и ошибок.

### Дополнительно

- В `desktop/` добавлены исходники экспериментальной Tauri-оболочки. Она пока
  не считается готовым клиентом и не включена в релизные артефакты.
- Нативный Swift-клиент пока не заменяет весь PyQt-интерфейс: Live-захват,
  LLM-запросы, история и управление API в нём ещё не подключены.

### Благодарность

Огромное спасибо [@Baggrisha](https://github.com/Baggrisha) за масштабную
переработку Desktop UI и создание нативного macOS-клиента.

## English

### Native Swift client for macOS

- Added `GigaAMLiquid` for Apple Silicon and macOS 13+.
- The client accepts local media and URLs, displays progress, supports queued
  transcription and stop-after-current-file, and exports TXT and SRT.
- Light/dark appearance and Russian/English language settings persist between
  launches.
- GitHub Actions builds the Swift package as an `.app`, verifies its
  architecture and signature, and publishes
  `GigaAMLiquid-macos-arm64-v2.0.zip`.
- A self-contained `GigaAMLiquid-macos-arm64-offline-v2.0.zip` is also
  published with the frozen runtime and model files; it requires neither a
  separate Python installation nor model downloads.
- Both Liquid archives contain only the built applications and required runtime
  files; the repository source tree is not included in the release ZIPs.

The Swift client delegates inference to a frozen companion application. The
regular archive downloads models on first use, while the offline archive ships
them. The app is ad-hoc signed and is not notarized.

### Redesigned Desktop UI

- Redesigned the processing, Live, LLM, settings and support screens.
- Added adaptive cards, refreshed light and dark themes, improved localization
  and denser layouts.
- The Live screen now has a three-pane layout, a recording timer, clearer
  transport controls and state feedback.
- Real URL media downloads and transcription are connected to the existing
  Python worker with progress, results, export and error handling.

### Additional changes

- Added the source of an experimental Tauri shell under `desktop/`. It is not
  considered production-ready and is not shipped as a release artifact.
- The native Swift client does not yet replace every PyQt feature: live capture,
  LLM requests, history and API management remain unavailable there.

### Thanks

Many thanks to [@Baggrisha](https://github.com/Baggrisha) for the substantial
Desktop UI redesign and the native macOS client.
