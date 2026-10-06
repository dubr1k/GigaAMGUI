# GigaAM Transcriber 1.5.3

Точечный релиз по [issue #49](https://github.com/dubr1k/GigaAMGUI/issues/49):
на вкладке Live под Linux список **системных источников был всегда пуст**, и
старт сессии с `system` падал с `CaptureUnavailable: No PipeWire/PulseAudio
monitor source is available` — при том что `pactl list sources short` показывал
шесть мониторов.

## Русский

### Исправлено

- **Мониторы PipeWire/PulseAudio не попадали в перечисление PortAudio.**
  `SoundDeviceCapture.devices()` искал устройства с подстрокой `monitor` в
  имени. Мониторы — виртуальные источники звукового сервера, а PortAudio в этой
  конфигурации ходит в ALSA напрямую: он перечисляет физические устройства и
  агрегаты `pulse`/`default`, и мониторов там нет в принципе. Фильтр не
  «промахивался» — фильтровать было нечего. Для SYSTEM на Linux список теперь
  собирается у самого сервера (`pactl list sources short`, фильтр по
  `*.monitor`) и мерджится с перечислением PortAudio. Монитор текущего sink по
  умолчанию предвыбирается. Обход через именованный ALSA-pcm в `~/.asoundrc`
  продолжает работать и не появляется в списке дважды.
- **Открытие выбранного монитора.** Поток открывается на ALSA-агрегате `pulse`
  и сразу перецепляется на монитор через `pactl move-source-output` — штатный
  механизм сервера, затрагивающий только нашу запись, а не системные умолчания
  (`PULSE_SOURCE` для этого не работает). Своя запись опознаётся как та, которой
  не было до открытия потока: микрофон того же процесса идёт через тот же
  сервер, и «взять последнюю» перецепило бы микрофон, если системный звук
  стартовал первым.
- **Неудачное перецепление останавливает старт сессии.** Поток, оставшийся на
  источнике по умолчанию, писал бы в `system.flac` микрофон — это хуже, чем
  сессия, которая не стартовала.

### Улучшено

- **`--selfcheck` на Linux сообщает, сколько мониторов видит сборка.** По логу
  из отчёта пользователя сразу видно, пустой список — это «нет pactl» или «нет
  мониторов»; гейтом это быть не может, на CI-раннере звукового сервера нет.
- **Документация.** README и подсказки об установке называют `pulseaudio-utils`
  (pactl) и `libasound2-plugins` (ALSA-плагин pulse): вшить их в бинарник, как
  PortAudio в #47, нельзя — это системные пакеты.

### Тесты

- `test_linux_system_devices_include_monitors_absent_from_portaudio` — мониторы
  появляются в списке при том самом перечислении PortAudio, что приложил
  репортёр.
- `test_linux_pulse_monitor_opens_the_aggregate_and_moves_the_stream` —
  открывается индекс `pulse`, перецепляется новая запись, а не микрофон того же
  процесса.
- `test_linux_pulse_monitor_start_fails_instead_of_recording_the_microphone` и
  `test_linux_without_pactl_reports_how_to_enumerate_monitors`.
- Поддельный `pactl` отдаёт настоящий формат вывода, то есть проверяется
  парсинг, а не мок.

Диагноз, A/B-воспроизведение и проверка `PULSE_SOURCE` — @alexanderlazutkin.

## English

### Fixed

- **PipeWire/PulseAudio monitors never appeared in the PortAudio enumeration.**
  `SoundDeviceCapture.devices()` matched device names containing `monitor`, but
  monitors are virtual sources of the sound server while PortAudio talks to ALSA
  directly and enumerates physical devices plus the `pulse`/`default`
  aggregates. The filter had nothing to match. Linux now enumerates SYSTEM
  sources through the server itself (`pactl list sources short`, filtered to
  `*.monitor`) and merges them into the PortAudio list; the default sink's
  monitor is preselected, and a monitor exposed as a named ALSA PCM via
  `~/.asoundrc` still works and is not listed twice.
- **Opening a monitor.** The stream is opened on the ALSA `pulse` aggregate and
  immediately moved onto the chosen monitor with `pactl move-source-output` —
  the server's own mechanism, affecting only our stream and never the user's
  defaults. Our recording is identified as the source-output that did not exist
  before the stream opened, because the microphone of the same process goes
  through the same server.
- **A failed move stops the session start** instead of recording the default
  input into `system.flac`.

### Improved

- `--selfcheck` logs how many monitor sources the build can see on Linux.
- README and install hints name `pulseaudio-utils` and `libasound2-plugins`.

Reported and diagnosed by @alexanderlazutkin.
