# GigaAM Transcriber 1.5.4

Точечный релиз по итогам проверки 1.5.3 на реальной машине с PipeWire:
[issue #49](https://github.com/dubr1k/GigaAMGUI/issues/49) (перецепка на монитор
падала на локализованной системе) и
[issue #50](https://github.com/dubr1k/GigaAMGUI/issues/50) (`mix.flac`
растягивался в 4,87 раза и терял микрофон).

## Русский

### Исправлено

- **Перецепка потока на монитор падала из-за локали (#49).** `pactl` переводит
  свой вывод. В `ru_RU` заголовок записи — `Выход источника №101` вместо
  `Source Output #101`, а sample-spec — `s32le 2-канальный 4800` вместо
  `s32le 2ch 48000Hz`. Обе регулярки в `src/live/capture/pulse.py` написаны под
  C-локаль, поэтому `source_outputs()` всегда возвращал пустое множество: своя
  запись не находилась, `attach_to_monitor()` вырабатывал двухсекундный таймаут
  и возвращал False, а старт системного захвата честно падал с `Could not move
  the capture stream to monitor source ...`. В `system.flac` при этом
  оставалось около двух секунд входа по умолчанию. Заодно частота монитора
  читалась как 4800 вместо 48000 и спасалась только запасным значением
  48000/2.

  Локаль теперь задаёт приложение, а не окружение пользователя: `pactl`
  вызывается с `LC_ALL=C` (и пустым `LANGUAGE`). Обход — запуск всего бинарника
  под `LC_ALL=C` — больше не нужен.

- **`mix.flac` растягивался в разы и терял микрофон (#50).** Микшер брал
  позицию чанка из метки его прихода и делал длину выходного блока равной
  «сдвиг между источниками + длина звука». Сдвиг там знака не имеет — блок
  считается от `min` метки, — поэтому даже нулевое в среднем дрожание меток
  добавляло `abs(сдвиг)` тишины в **каждую** пару чанков. На сессии 98,6 с из
  ~5 300 пар со средним лагом ~90 мс это дало файл 480,6 с (×4,87), в котором
  речь тонула между растущими промежутками тишины, при идеальных `mic.flac` и
  `system.flac`. Проявлялось только при живом системном источнике, то есть
  ровно после фикса #49; раньше системный захват умирал через 2 с и микс писался
  из одного источника, где сдвига нет.

  Изменено два места:

  - `LiveSession._normalize_mix_timestamp()` ставит чанк на дорожку микса по
    смещению сэмплов, а не по часам. Смещения приходят из `SourceTimeline`,
    который уже зашил разрывы и срезал перекрытия, поэтому они растут ровно со
    звуком и не дрожат. Момент старта каждого источника, для которого часы всё
    ещё нужны, учитывается как и раньше — по первому чанку.
  - `AlignedMixer` ведёт непрерывную выходную дорожку и продвигает её только на
    тот звук, который реально отдал. Аудио, чей парный источник ещё не догнал,
    придерживается в буфере и дописывается, когда тот придёт, — ничего не
    теряется и ничего не пишется дважды. Постоянный сдвиг между источниками
    стоит одного блока тишины в начале сессии, а не блока набивки на каждую
    пару.

- **Формат дорожки микса фиксируется первым блоком.** Раньше формат брался у
  того источника, который оказался в блоке: замолчавший на полсекунды микрофон
  передавал микс 48-килогерцовому системному источнику, и запись падала на
  `recording track format changed`, теряя дорожку целиком.

- **Хвост микса дописывается при остановке.** Придержанное аудио, чей парный
  источник уже не придёт, теперь сбрасывается в файл, а не отбрасывается.

### Тесты

- `test_linux_monitors_are_enumerated_under_a_localized_pactl` и
  `test_linux_localized_pactl_still_moves_the_capture_stream` — поддельный
  `pactl` переводит вывод ровно так, как это делает `ru_RU`, если запрос пришёл
  не с `LC_ALL=C`; `test_linux_pactl_is_always_asked_in_the_c_locale`
  проверяет саму фиксацию локали.
- `test_mixer_does_not_pay_the_source_offset_once_per_pair` — 1000 фреймов
  звука при сдвиге 20 мс дают 1020 фреймов дорожки, а не 1200, как раньше.
- `test_mixer_keeps_every_microphone_frame_when_the_peer_lags` — микрофон в
  миксе идёт подряд; на старом коде он занимал треть дорожки, остальное было
  тишиной.
- `test_mixer_jitter_does_not_accumulate_across_blocks`,
  `test_mixer_output_format_is_fixed_by_the_first_block`.
- Сессионные тесты `test_staggered_source_callbacks_produce_timestamp_aligned_mix`
  и `test_small_normalized_clock_jitter_and_drift_keeps_mix_bounded` переписаны
  под новый контракт: длина микса равна длине звука.

Диагноз локали (включая `LC_ALL=C pactl | grep -c` A/B), измерения `mix.flac` и
разбор выравнивания пар — @alexanderlazutkin.

## English

### Fixed

- **Moving the capture stream onto a monitor failed under a localized `pactl`
  (#49).** `pactl` translates its output: on `ru_RU` the record header reads
  `Выход источника №101` and the sample spec `s32le 2-канальный 4800`. Both
  parser regexes assume the C locale, so `source_outputs()` always came back
  empty, our own recording was never found, `attach_to_monitor()` burned its
  two-second timeout and system capture refused to start. The application now
  runs `pactl` with `LC_ALL=C` itself, so the user's locale no longer matters.

- **`mix.flac` was stretched several times over and lost the microphone (#50).**
  The mixer positioned chunks by their arrival timestamps and sized each output
  block as `offset + length`. That offset has no sign, so even zero-mean
  timestamp jitter added `abs(offset)` of silence to *every* pair: a 98.6 s
  session came out as a 480.6 s file (x4.87) with the speech buried between
  growing runs of silence, while `mic.flac` and `system.flac` were perfect.
  Chunks are now placed by sample offset rather than by wall clock, and the
  mixer keeps a continuous output timeline that advances only by the audio it
  emits — audio whose peer has not caught up is held back and written once the
  peer arrives.

- **The mix track's format is fixed by its first block**, so a microphone that
  goes quiet for a moment can no longer hand the track to the 48 kHz system
  source and cost the whole recording.

- **The mixer's tail is flushed at stop** instead of being dropped.

Reported, measured and diagnosed by @alexanderlazutkin.
