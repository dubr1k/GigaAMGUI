# GigaAM Transcriber 2.1.4

Классический интерфейс PyQt вернулся. Это ответ на issue #54: редизайн 2.0
ломал вёрстку на Windows и Linux, и все правки 2.1.3 лечили симптомы. В 2.1.4
оболочка 1.6 восстановлена целиком, а страницы 2.0 сохранены вкладками.

## Изменено

- **Оболочка 1.6.** Строка меню (Файл / Вид / Настройки / Справка), заголовок
  с переключателями языка «EN/RU» и темы ☀/🌙, видимые вкладки, нумерованные
  секции «1. Выбор файлов … 5. Форматы вывода», большая кнопка «ЗАПУСТИТЬ
  ОБРАБОТКУ», карточка прогресса, нейтральные тёмная и светлая палитры.
  Боковая панель, карточки, бутафорское «окно macOS» внутри окна и
  декоративные значки 2.0 убраны.
- **Страницы 2.0 остались** — вкладками того же окна: «Обработка» с
  просмотром результата (плеер, текст/SRT/диаризация/итог/JSON), «Live»,
  «LLM», «API», «Журнал» (таблица событий + технический лог), «Настройки»
  с семью разделами.
- **Системный шрифт на всех платформах.** Объявление
  `font-family: -apple-system, "SF Pro Text", "Segoe UI", Arial` в таблице
  стилей Qt резолвил в Arial для подписей, чекбоксов и списков даже на macOS.
  Убрано; на Windows-раннере `QFontInfo` подтверждает Segoe UI во всех
  виджетах.
- **Масштаб от платформенной базы шрифта:** Windows 9 pt → 1.06, macOS
  13 pt → 1.08, Linux 10 pt → 1.05 (раньше делили на 12 pt, и Windows
  зажимался к минимуму 0.85).

## Исправлено

- **Страница Live.** Боковые панели были зажаты до 175/180 pt, поля
  устройств сжимались до пары букв, «Записывать дорожку …» обрезалось,
  счётчики строк/символов в нативном стиле Windows не показывали цифр.
  Панели расширены, поля растягиваются, транскрипт занимает свободную
  высоту.
- **Публикация релизов.** Ассеты заливаются по одному с повторами
  (`scripts/publish_release_assets.sh`), релиз остаётся черновиком, пока не
  прикреплены все 11 файлов; ручной workflow `publish-release.yml`
  перепубликует из артефактов готового билда без пересборки.

## Добавлено

- **Скриншоты интерфейса с CI.** `scripts/render_gui_screenshots.py` и
  workflow `ui-screenshots.yml` рендерят все страницы PyQt на Windows
  (windows11, Segoe UI 9 pt) и Linux (Xvfb) в обеих темах и пишут
  `environment.txt` с реально выбранным шрифтом каждого виджета.

Спасибо @eXpressionist за скриншоты и диагностику.

## Проверено

- `tests/test_gui_classic_shell.py`, `tests/test_gui_live_layout.py`,
  `tests/test_gui_platform_font_scale.py`; полный pytest и ruff чистые.
- Рендер всех шести страниц в обеих темах при 9 pt и 13 pt офскрин и на
  Windows/Linux-раннерах.

## Packaging

- Версии PyQt, AppKit, Tauri, npm и Cargo синхронизированы на `2.1.4`.

---

The classic 1.6 PyQt shell is back (menu bar, header with language/theme
switches, visible tabs, numbered sections, neutral palette); the 2.0
sidebar/cards/mock-window design that broke Windows and Linux layouts
(issue #54) is gone. The 2.0 pages remain as tabs: result viewer with a
player, journal table, API, seven-section Settings. The stylesheet no longer
pins a font-family list Qt resolved to Arial; the UI scale uses a per-platform
font baseline. Live pane widths fixed. Release assets upload sequentially with
retries; a CI workflow renders every page on Windows and Linux runners.
