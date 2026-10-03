# GigaAM Transcriber 2.6.1

Патч-релиз Liquid: приложение больше не падает при закрытии окна.

## Исправлено

- **Liquid аварийно завершался при закрытии окна крестиком.** В 2.6.0 нажатие на красную кнопку закрытия приводило к падению (Segmentation fault) и отчёту о сбое macOS вместо обычного выхода. Причина в том, что macOS освобождала окно при закрытии, а приложение затем, завершаясь, обращалось к нему, чтобы сохранить поле, которое ещё редактировалось. Теперь окно живёт до конца работы приложения. Выход через ⌘Q и сохранение Live-сессии при выходе работают как раньше.

## Что проверить

- Запустите GigaAM Liquid и закройте окно крестиком: приложение должно закрыться без отчёта о сбое.
- Начните вводить ключ API в настройках LLM и сразу закройте окно: после повторного запуска ключ сохранён.
- Во время записи Live закройте окно: приложение спросит, остановить ли запись, и сохранит сессию.

PyQt, web, TUI и worker не менялись: исправление касается только Swift-клиента Liquid.

---

## English

Liquid patch release: the app no longer crashes when its window is closed.

### Fixed

- **Liquid crashed when the window was closed with the close button.** In 2.6.0 the red close button ended in a crash (Segmentation fault) and a macOS crash report instead of a normal quit. macOS released the window when it closed, and the app then touched it while quitting to save a field that was still being edited. The window now lives until the app exits. Quitting with ⌘Q and saving a Live session on quit work as before.

### What to check

- Launch GigaAM Liquid and close the window with the close button: the app should quit without a crash report.
- Start typing an API key in the LLM settings and close the window right away: after relaunching, the key is saved.
- Close the window while Live is recording: the app asks whether to stop recording and saves the session.

PyQt, web, the TUI and the worker are unchanged: the fix touches only the Liquid Swift client.
