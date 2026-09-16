# GigaAM Transcriber 2.0.5

Исправляющий релиз нативного macOS-клиента GigaAMLiquid: крэш при английском
интерфейсе, «мёртвая» кнопка запуска, число спикеров для Sortformer и выбор
диаризованных форматов.

Релизы 2.0–2.0.4 отозваны с GitHub: это первый опубликованный релиз ветки 2.x.
Он включает всё, что принесла 2.0 (переработанный Desktop UI и нативный
Swift-клиент GigaAMLiquid для Apple Silicon), и исправления 2.0.1–2.0.4; подробности
по каждой версии в `docs/CHANGELOG.md`.

## Исправления

- **Крэш при английском языке.** Словарь переводов содержал повторяющиеся
  ключи («Файл», «Копировать», «Проект на GitHub»). Swift останавливает
  процесс на литерале словаря с дубликатами, а словарь ленивый и трогается
  только при `settings.language = English`, поэтому русский интерфейс работал,
  а английский падал на старте с `Fatal error: Dictionary literal contains
  duplicate keys`. Дубликаты убраны, добавлен тест на уникальность ключей.
- **Кнопка «Запустить обработку» не реагировала.** Поле «Папка сохранения»
  сохраняется на каждое нажатие клавиши; стёртое поле оставляло в настройках
  пустую строку, которая не заменялась значением по умолчанию. Кнопка
  выключалась, но у чёрной primary-кнопки не было видимого disabled-состояния.
  Пустой путь теперь означает `~/Documents/GigaAM`, отключённые кнопки
  приглушены.
- **Sortformer и число спикеров.** Sortformer определяет спикеров сам (до 4),
  ручное значение он не принимает: 5–6 роняли файл с «Sortformer поддерживает
  не более 4 спикеров», 1–4 молча игнорировались. Для Sortformer список
  «Кол-во спикеров» заблокирован и сброшен на «Авто», `num_speakers` в worker
  не передаётся; при смене движка или выключении диаризации сохранённое
  значение сбрасывается, как в PyQt-клиенте.

## Добавлено

- **Диаризованные форматы.** В карточке «Форматы вывода» появились
  «Диаризация (.txt)» и «Диар. + таймкоды», как в PyQt; доступны только при
  включённой диаризации.

## Проверено

- Сквозной прогон в GigaAMLiquid: файл с Sortformer и сохранённым «6
  спикеров» (сценарий, который раньше падал) обработан, созданы `.txt`,
  `_timecodes.txt`, `_diarize.txt`, `_diarize_timecodes.txt`.
- Запуск при `settings.language = English` больше не падает.

## Packaging

- Версии PyQt, AppKit, Tauri, npm и Cargo синхронизированы на `2.0.5`.

## Благодарность

Огромное спасибо [@Baggrisha](https://github.com/Baggrisha) за масштабную
переработку Desktop UI и создание нативного macOS-клиента GigaAMLiquid в 2.0.

---

This patch release fixes the native GigaAMLiquid client: a launch crash when
the UI language is English (duplicate keys in the translation dictionary
literal), a Start button that looked enabled but was silently disabled by an
empty output-folder setting, a manual speaker count offered for Sortformer
(which auto-detects up to 4 speakers and rejected 5–6), and adds the
diarization output formats to the Output formats card.

Releases 2.0–2.0.4 were withdrawn from GitHub, so this is the first published
2.x release; it carries everything 2.0 introduced (the redesigned Desktop UI
and the native GigaAMLiquid client for Apple Silicon) plus the 2.0.1–2.0.4
fixes listed in `docs/CHANGELOG.md`.

Many thanks to [@Baggrisha](https://github.com/Baggrisha) for the substantial
Desktop UI redesign and the native macOS client.
