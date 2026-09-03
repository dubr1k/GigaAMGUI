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

### Тесты

- `test_linux_sounddevice_caps_capture_channels_for_pulse_aggregate` открывает
  захват mic и system на устройстве с `max_input_channels=32` и проверяет, что
  и параметры потока, и доставленные кадры — стерео.

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

### Tests

- `test_linux_sounddevice_caps_capture_channels_for_pulse_aggregate` starts mic
  and system capture on a device reporting `max_input_channels=32` and asserts
  that both the stream parameters and the delivered frames are stereo.

Спасибо [@alexanderlazutkin](https://github.com/alexanderlazutkin) за подробный
разбор с воспроизведением на libsndfile — диагноз в issue был точным до строки.
