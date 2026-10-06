# GigaAM Transcriber 2.1.1

GigaAMLiquid стал самостоятельным приложением: в архиве один `GigaAMLiquid.app`,
который можно перенести куда угодно.

## Что изменилось

- **Один бандл вместо двух.** Замороженный Python-рантайм
  (`GigaAMTranscriber.app`), который выполняет распознавание, диаризацию,
  импорт медиа и LLM, теперь встроен в `GigaAMLiquid.app/Contents/Resources`.
  Раньше он лежал рядом с приложением, и перенос `GigaAMLiquid.app` отдельно
  ломал запуск с «Python не найден».
- **Офлайн-архив** кладёт модели туда же, в
  `GigaAMLiquid.app/Contents/Resources/models/hf`; worker запускается с
  `HF_HUB_OFFLINE=1` и в сеть не ходит.
- **Порядок поиска рантайма.** Сначала собственный бандл, затем старая
  раскладка «companion рядом» (архивы 2.0–2.1.0), затем исходники проекта
  (`GIGAAM_PYTHON`, `.venv`, `python3`); `GIGAAM_PROJECT_ROOT` по-прежнему
  переопределяет поиск.
- **Рабочие файлы вне бандла.** Worker работает из
  `~/Library/Application Support/GigaAMLiquid`, поэтому `processing_stats.json`
  не попадает внутрь подписанного приложения и подпись остаётся целой.

## Проверено

- Самостоятельный бандл, вынесенный из репозитория, без переменных окружения:
  батч-транскрибация файла с диаризацией (четыре файла результата), live-запись
  с микрофона с вопросом ассистенту и сохранением сессии; статистика ушла в
  Application Support, `codesign --verify --deep --strict` после работы
  проходит.
- Офлайн-раскладка: вложенный companion с пустым `HF_HOME` и
  `HF_HUB_OFFLINE=1` прошёл батч- и live-smoke на моделях из
  `Contents/Resources/models/hf`.
- pytest, ruff, `swift build -c release` без предупреждений.

## Packaging

- Версии PyQt, AppKit, Tauri, npm и Cargo синхронизированы на `2.1.1`.

---

GigaAMLiquid 2.1.1 ships as a single self-contained `GigaAMLiquid.app`: the
frozen Python worker (`GigaAMTranscriber.app`) and, in the offline archive, the
model files live inside `Contents/Resources`, so the app can be moved anywhere.
The runtime lookup prefers the embedded companion, then the old side-by-side
layout, then a source checkout. Worker scratch files go to
`~/Library/Application Support/GigaAMLiquid` instead of the signed bundle.
