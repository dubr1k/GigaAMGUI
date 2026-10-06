# GigaAM Transcriber 1.5.0

Релиз про память и скорость. Диагностика началась с жалобы на «мощный жор
оперативной памяти» после транскрибации с диаризацией на Mac. Оказалось, что
приложение удерживало десятки гигабайт, которые не были видны в `ps` и `top`, —
и попутно считало VAD на процессоре там, где рядом простаивал ускоритель.

Все числа ниже измерены на одной и той же записи длительностью 117 минут, до и
после правок, с идентичным результатом распознавания (428 сегментов ASR, 1608
сегментов диаризации).

| Показатель | Было | Стало |
|---|---|---|
| Системной памяти за цикл | 37.1 ГБ | 4.9 ГБ |
| Буферный пул MLX после ASR | 37.91 ГБ | 0.00 ГБ |
| Память torch после диаризации | 46.60 ГБ | 2.78 ГБ |
| ASR + VAD | 598 с | 73 с |
| Диаризация | 33 с | 16 с |

## Русский

### Исправлено

- **Буферный пул MLX рос без ограничений.** `unload()` искал `clear_cache` в
  пакете `mlx`, тогда как функция живёт в `mlx.core`. Обращение всегда
  возвращало `None`, а промах молча глушился пустым `except`, поэтому пул не
  возвращался системе ни на одном пути выполнения. На записи в 117 минут он
  доходил до 37.91 ГБ при реальной пиковой потребности 1.65 ГБ. На Apple
  Silicon память общая, так что расходовалась именно оперативная память, причём
  в RSS процесса рост не отображался — поэтому проблему и не замечали.
  Дополнительно пул теперь подрезается каждые 32 окна декодера: окна разной
  длины дают буферы разного размера, MLX кэширует каждый размер отдельно, и на
  часовой записи таких окон сотни.
- **Модель диаризации оставалась в памяти до конца работы приложения.**
  `SortformerDiarizationManager` держит загруженную модель NeMo в словаре
  уровня класса, но не переопределял `unload()`; унаследованный метод обнулял
  только ссылку экземпляра. Это касалось всех платформ, а не только macOS: на
  CUDA речь о примерно 1.5 ГБ удержанной видеопамяти на карте фиксированного
  объёма, и затрагивало все интерфейсы, которые честно вызывают `unload()`
  (web, api, cli).
- **Аудио для pyannote грузилось в float64.** Патч предзагружает файл целиком,
  а `soundfile` по умолчанию отдаёт float64, который затем всё равно
  приводится к float32 — обе копии жили одновременно. На записи в 117 минут это
  0.837 ГБ плюс 0.418 ГБ при исходнике в 214 МБ PCM_16 на диске.
- **GUI не освобождал кэши после обработки.** Web-, api- и cli-интерфейсы
  освобождали то, что заняли, а пакетная обработка в GUI не делала ничего, при
  этом её `ModelLoader` создаётся один раз при запуске и живёт всю сессию.
  Теперь после пачки освобождаются пулы и MLX, и torch: диаризация работает
  через torch, её кэш отдельный, и он один удерживал около 8 ГБ.
- **Кнопку «Очистить» нельзя было использовать, чтобы забыть папку.** Фикс
  1.4.4 научил её сбрасывать запомненную папку источника, но поставил этот
  сброс за уже существующим условием «очередь непустая». После перезапуска
  очередь пуста, а папка запомнена — кнопка выходила на первой же строке и до
  сброса не доходила, поэтому папка подставлялась при каждом запуске и убрать
  её было нечем. Теперь выход происходит только когда чистить действительно
  нечего: ни файлов в очереди, ни запомненной папки. То же исправлено для
  списка транскриптов LLM, где стояло такое же условие.

### Изменено

- **VAD на Apple Silicon считается на ускорителе.** `ASR_VAD_DEVICE` по
  умолчанию теперь `auto` вместо `cpu`. Прежний дефолт защищал от нехватки
  памяти, когда pyannote VAD оказывается рядом с GigaAM на одном ускорителе.
  Это верно там, где у ускорителя свой ограниченный бюджет, поэтому **на CUDA
  дефолт остаётся `cpu`**. На Apple Silicon память общая, отдельного бюджета
  нет: замер показал, что VAD на MPS даёт *меньший* пик, чем на CPU (4.52 ГБ
  против 4.88 ГБ), и работает вчетверо быстрее. Основной источник риска —
  неограниченный пул MLX — устранён в этом же релизе.

  Переменная окружения `ASR_VAD_DEVICE` по-прежнему переопределяет выбор, а при
  сбое VAD сохраняется штатный откат на разбиение с перекрытием.

### Производительность

- Убран повторный прогон ffmpeg. `processor` уже конвертирует вход в 16 кГц
  моно WAV, после чего MLX-бэкенд запускал ffmpeg на этом же файле ещё раз:
  лишний полный проход по аудио и одновременно живущие байты вывода
  подпроцесса и массив float32. Для форматов, не совпадающих с ожидаемыми,
  сохранён откат на ffmpeg.

## English

### Fixed

- **The MLX buffer pool grew without bound.** `unload()` looked up
  `clear_cache` on the `mlx` package, but the function lives in `mlx.core`. The
  lookup always returned `None` and the miss was swallowed by a bare `except`,
  so the pool was never returned to the system on any code path. On a
  117-minute recording it reached 37.91 GB while peak actual demand was 1.65
  GB. On Apple Silicon this is unified memory, so it was system RAM being
  consumed — and the growth never showed up in the process RSS, which is why it
  went unnoticed. The pool is now also trimmed every 32 decoder windows:
  windows differ in length, MLX caches each buffer size separately, and an
  hour-long file is hundreds of windows.
- **The diarization model stayed resident for the life of the process.**
  `SortformerDiarizationManager` keeps the loaded NeMo model in a class-level
  dict but never overrode `unload()`; the inherited one only cleared the
  instance reference. This affected every platform, not just macOS: on CUDA it
  means roughly 1.5 GB of VRAM held on a fixed-size card, and it hit every
  front-end that does call `unload()` (web, api, cli).
- **pyannote audio was loaded as float64.** The patch preloads the whole file,
  and `soundfile` defaults to float64, which is then converted to float32
  anyway — both copies alive at once. On a 117-minute recording that is 0.837
  GB plus 0.418 GB, from a 214 MB PCM_16 source on disk.
- **The GUI never released its caches after processing.** The web, api, and cli
  front-ends released what they allocated; the GUI batch path did nothing, and
  its `ModelLoader` is created once at startup and lives for the whole session.
  Both the MLX and the torch pools are now released after a batch: diarization
  runs through torch, its cache is separate, and it alone held about 8 GB.
- **"Clear" could not be used to forget the folder.** The 1.4.4 fix taught it
  to drop the remembered source folder, but placed that behind the existing
  "queue is not empty" guard. After a restart the queue is empty while the
  folder is still remembered, so the button returned on its first line and
  never reached the reset — the folder was filled in on every launch with no
  way to clear it. It now only returns early when there is genuinely nothing to
  clear: no queued files and no remembered folder. The LLM transcript list had
  the same guard and is fixed too.

### Changed

- **VAD runs on the accelerator on Apple Silicon.** `ASR_VAD_DEVICE` now
  defaults to `auto` instead of `cpu`. The previous default guarded against
  running out of memory with pyannote VAD sitting next to GigaAM on one
  accelerator. That holds where the accelerator has its own limited budget, so
  **CUDA keeps `cpu`**. Apple Silicon shares memory and has no separate budget:
  measured end to end, VAD on MPS peaked *lower* than on CPU (4.52 GB vs 4.88
  GB) while running four times faster. The main source of that risk — the
  unbounded MLX pool — is fixed in this same release.

  `ASR_VAD_DEVICE` still overrides the choice, and a failing VAD still falls
  back to overlap chunking as before.

### Performance

- Removed a redundant ffmpeg pass. `processor` already converts the input to a
  16 kHz mono WAV, and the MLX backend then ran ffmpeg on that very file again:
  an extra full pass over the audio, holding the subprocess output bytes and
  the float32 array at the same time. Inputs that do not match the expected
  format still fall back to ffmpeg.
