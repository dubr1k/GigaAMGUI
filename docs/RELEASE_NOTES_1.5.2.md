# GigaAM Transcriber 1.5.2

Точечный релиз по [issue #48](https://github.com/dubr1k/GigaAMGUI/issues/48):
после 1.5.1 live-сессия на Linux стартовала и распознавала речь, но запись
исходного аудио падала на каждом чанке — `mic.flac`, `system.flac` и `mix.flac`
оставались нулевыми, а `live.log` наполнялся `LibsndfileError: Format not
recognised` (в одной из сессий репортёра — 15 255 ошибок).

## Русский

### Исправлено

- **Запись live-сессии во FLAC падала на агрегатах PulseAudio/PipeWire.**
  `SoundDeviceCapture.start()` открывал поток с `channels` = `max_input_channels`
  устройства. PortAudio отдаёт агрегаты `pulse`/`default` (и именованные
  pulse-pcm, используемые как монитор системного звука) с 32 входными каналами,
  и агрегат обычно является устройством по умолчанию. `LiveRecorder` пытался
  открыть 32-канальный FLAC, а libsndfile поддерживает максимум 8 — отсюда
  `Format not recognised` для обоих источников и нулевые файлы. Захват теперь
  открывается максимум в стерео: распознаванию всё равно нужно моно, а писать
  32 канала в PCM_24 бессмысленно и по объёму.
- **Переполнение очереди захвата.** 32-канальный поток давал x16 объёма данных
  против стерео, и при активном системном звуке очередь захлёбывалась
  (`capture event [system/overflow]: capture queue full; dropped_frames=...`).
  Уходит вместе с лишними каналами.

### Укреплено (чтобы тот же класс ошибки не повторился)

- **`SessionRecorder` срезает запись до 8 каналов** — потолка FLAC в libsndfile.
  Захват ограничен стерео, но запись больше не зависит от этого: бэкенд с
  неправдоподобным числом каналов теперь теряет лишние дорожки, а не всю запись.
- **Очередь захвата ограничена байтами, а не кадрами.** Лимит в кадрах молча
  масштабировался вместе с числом каналов: «480 000 кадров» на 32 каналах — это
  в 16 раз больший буфер, чем задумано.
- **Запись источника отключается после 5 подряд неудачных записей.** Писатель,
  который не смог открыть файл, не начнёт работать на пятитысячном чанке;
  повторные попытки давали 15 255 одинаковых строк в `live.log`. Распознавание
  продолжается, пользователь получает одно понятное уведомление.
- **`--selfcheck` пишет настоящий FLAC и читает его обратно.** Гейт 1.5.1
  проверял только импорт модулей захвата — этого не хватило, чтобы поймать
  #48. Проверка выполняется и в `--live-capture-smoke`, то есть в джобе полной
  macOS `.app`.

### Тесты

- `test_linux_sounddevice_caps_capture_channels_for_pulse_aggregate` открывает
  захват mic и system на устройстве с `max_input_channels=32` и проверяет, что
  и параметры потока, и доставленные кадры — стерео.
- Добавлены тесты на потолок каналов в рекордере, на байтовый лимит очереди, на
  отключение записи после серии сбоев (и на то, что одиночный сбой её не гасит),
  и на FLAC round-trip в selfcheck.
- Три устаревших теста приведены к текущему поведению, два — изолированы от
  личной конфигурации машины; `pytest tests/` и `ruff check .` зелёные целиком.

## English

### Fixed

- **Live session recording failed on PulseAudio/PipeWire aggregates.**
  `SoundDeviceCapture.start()` opened the stream with the device's
  `max_input_channels`. PortAudio exposes the `pulse`/`default` aggregates (and
  named pulse PCMs used as the system-audio monitor) with 32 input channels, and
  the aggregate is usually the default device. `LiveRecorder` then tried to open
  a 32-channel FLAC, which libsndfile caps at 8 — hence `Format not recognised`
  for both sources and zero-byte files. Capture is now opened in stereo at most:
  recognition needs mono anyway, and writing 32 channels of PCM_24 is pointless
  in size terms too.
- **Capture queue overflow.** A 32-channel stream carried 16x the data of stereo
  and flooded the queue whenever system audio was active
  (`capture event [system/overflow]: capture queue full; dropped_frames=...`).
  It goes away with the extra channels.

### Hardened (so the same class of fault cannot repeat)

- **`SessionRecorder` clamps recording to 8 channels**, libsndfile's FLAC
  ceiling. Capture is stereo-capped, but the write side no longer depends on
  that: a backend reporting an implausible channel count now loses the extra
  tracks rather than the whole recording.
- **The capture queue is bounded by bytes, not frames.** A frame bound scaled
  the memory ceiling with the channel count: "480 000 frames" at 32 channels is
  a 16x larger buffer than intended.
- **Source recording is given up on after 5 consecutive failures.** A writer
  that cannot open its file will not start working on chunk 5000; retrying per
  chunk produced 15 255 identical log lines. Recognition continues and the user
  gets one clear notice.
- **`--selfcheck` writes a real FLAC and reads it back.** The 1.5.1 gate only
  checked that the capture modules import, which was not enough to catch #48.
  The check also runs under `--live-capture-smoke`, i.e. in the full macOS
  `.app` job.

### Tests

- `test_linux_sounddevice_caps_capture_channels_for_pulse_aggregate` starts mic
  and system capture on a device reporting `max_input_channels=32` and asserts
  that both the stream parameters and the delivered frames are stereo.
- New tests cover the recorder's channel ceiling, the byte-bounded queue, the
  give-up-after-N-failures rule (and that a single transient failure does not
  trigger it), and the selfcheck FLAC round-trip.
- Three stale tests were brought in line with current behaviour and two were
  isolated from the developer's own machine configuration; `pytest tests/` and
  `ruff check .` are green in full.

Спасибо [@alexanderlazutkin](https://github.com/alexanderlazutkin) за подробный
разбор с воспроизведением на libsndfile — диагноз в issue был точным до строки.
