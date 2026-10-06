# GigaAM Transcriber 2.2.1

Исправительный релиз для GigaAM Liquid и журнала обработки. Главное: при
транскрибации в Liquid больше не выскакивает второе приложение, результаты по
умолчанию ложатся рядом с исходным файлом, а журнал читается человеком без
технического бэкграунда — во всех интерфейсах, по-русски и по-английски.

## Исправлено

- **Второе приложение при транскрибации в Liquid.** Замороженный companion на
  каждый файл запускал ещё один «GigaAM Transcriber», который прыгал в Dock и
  падал с `No module named src.utils.logger`, хотя сама транскрибация
  завершалась. Причина: `multiprocessing.resource_tracker` (его поднимают
  torch/pyannote) перезапускал `app.py` без `--native-worker`, а
  `multiprocessing.freeze_support()` — единственный способ, которым PyInstaller
  перехватывает такие перезапуски, — не вызывался. Теперь это первое действие
  `app.py`. В PyQt-сборках баг маскировался instance-lock'ом: лишняя копия
  просто молча выходила.
- **Liquid: папка результатов.** Пустое поле больше не подменяется на
  `~/Documents/GigaAM`. Пусто — значит «рядом с исходным файлом», как в PyQt.
  Папка, заданная руками или через «Изменить», сохраняется как раньше.
- **Liquid: кнопка «Журнал обработки»** не сдвигается, когда меняется ширина
  процентов («—» → «7%» → «100%»).

## Изменено

- **Журнал обработки — понятным языком, везде.** Строки формируются у источника
  (processor, model_loader, ASR-бэкенды, конвертер, worker), поэтому PyQt, web,
  CLI, TUI и Liquid показывают одно и то же. Вместо `DEBUG: Абсолютный путь`,
  `ASR сегментация: VAD, окон декодера: 1`, `MLX backend load requested`,
  `Пример структуры сегмента: keys=…`, `КРИТИЧЕСКАЯ ОШИБКА` и кодов FFmpeg:

  ```
  Файл 1 из 1: interview.mp3
  Длительность записи: 12:40
  Подготавливаем звук: interview.mp3 → WAV 16 кГц…
  Звук подготовлен.
  Очистка звука: лёгкая очистка от шума (режим auto)
    Почему: в записи постоянный фоновый шум
  Распознаём речь…
  Речь найдена: участков — 14, фрагментов для распознавания — 21
  Определяем, кто говорит (pyannote)…
  Говорящих найдено: 2
  Сохранён файл: interview.txt
  Готово за 1 мин 12 сек (подготовка звука 2.1 с, распознавание 68.4 с)
  ```

  Ничего не выбрасывается: пути, устройство, движок, причины очистки и
  подробности ошибок FFmpeg остаются. Deprecation-предупреждения библиотек
  (pyannote, torchaudio, transformers) больше не попадают в журнал; в Liquid они
  прикладываются к сообщению об ошибке отдельным блоком «Технические
  подробности для отчёта об ошибке».
- **Английский интерфейс переводит журнал целиком**, включая строки с именами
  файлов, числами и длительностями. Одна таблица `src/core/log_i18n.py` для
  PyQt и Liquid; Swift-копия генерируется скриптом и проверяется тестом.

## Добавлено

- **Liquid: drag & drop папок.** Перетащенная (или выбранная в диалоге) папка
  сканируется рекурсивно по тем же расширениям, что и в PyQt; скрытые файлы
  пропускаются, дубли схлопываются.

## Для разработчиков

- `GigaAMLiquidCore` — UI-независимый Swift-таргет (`MediaScan`,
  `LogTranslation`), покрыт `swift test`; job добавлен в `ci.yml`.
- Новые сообщения журнала: пишите по-русски и понятно у источника, добавляйте
  правило в `src/core/log_i18n.py`, запускайте
  `python scripts/gen_log_translation_swift.py`. Тесты
  `tests/test_processing_log_messages.py` ловят жаргон в log-вызовах,
  непереведённые причины предобработки и устаревший Swift-файл.
- `tests/test_app_freeze_support.py` фиксирует порядок: `freeze_support()`
  раньше worker'а, selfcheck и GUI.

## Что проверить после обновления

1. Liquid: запустить транскрибацию — в Dock не должно появляться второе
   приложение «GigaAM Transcriber».
2. Liquid: оставить поле папки пустым — результат появится рядом с исходным
   файлом; задать папку — результат в ней.
3. Liquid: перетащить папку с записями — все файлы (включая подпапки) в списке.
4. Любой интерфейс: открыть журнал обработки — без `DEBUG:`, кодов и английских
   технических фраз; переключить язык на English — журнал по-английски.

---

## English

A fix release for GigaAM Liquid and the processing log.

**Fixed**

- **A second application launched during transcription in Liquid.** The frozen
  companion started another "GigaAM Transcriber" for every file; it bounced in
  the Dock and crashed with `No module named src.utils.logger` even though the
  transcription itself finished. `multiprocessing.resource_tracker` (started by
  torch/pyannote) re-executed `app.py` without `--native-worker`, and
  `multiprocessing.freeze_support()` — the only hook PyInstaller uses to
  intercept such relaunches — was never called. It is now the first thing
  `app.py` does. PyQt builds masked the bug behind the instance lock.
- **Liquid: output folder.** An empty field is no longer replaced with
  `~/Documents/GigaAM`; empty means "next to the source file", as in PyQt. A
  folder you typed or picked is kept as before.
- **Liquid: the "Processing log" button** no longer shifts when the progress
  percentage changes width.

**Changed**

- **The processing log is written in plain language, everywhere.** Lines are
  produced at the source (processor, model loader, ASR backends, converter,
  worker), so PyQt, web, CLI, TUI and Liquid show the same thing: no more
  `DEBUG:` paths, `ASR segmentation: VAD, decoder windows`, `MLX backend load
  requested`, `CRITICAL ERROR` or bare FFmpeg return codes. Nothing is dropped —
  paths, device, engine, cleanup reasons and FFmpeg details stay; library
  deprecation warnings no longer reach the log.
- **The English UI translates the whole log**, including lines with file
  names, numbers and durations, from one shared table (`src/core/log_i18n.py`)
  used by PyQt and Liquid.

**Added**

- **Liquid: drag & drop folders.** A dropped (or chosen) folder is scanned
  recursively with the same extension list as PyQt.
- `swift test` for Liquid's UI-free logic (`GigaAMLiquidCore`) in CI.

**What to check after updating:** start a transcription in Liquid — no second
app in the Dock; leave the folder field empty — results land next to the
source; drop a folder — every file is listed; open the processing log in
English — it reads in English.
