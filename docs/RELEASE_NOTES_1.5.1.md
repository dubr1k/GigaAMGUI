# GigaAM Transcriber 1.5.1

Точечный релиз по [issue #47](https://github.com/dubr1k/GigaAMGUI/issues/47):
вкладка Live не работала в готовых сборках под Linux и macOS. Захват падал сразу
при старте сессии с сообщением «Install requirements-live-linux.txt» — файла с
таким именем в репозитории не существовало, а поставить пакет «снаружи» в
замороженное onefile-приложение невозможно.

## Русский

### Исправлено

- **Live-захват отсутствовал в сборках под Linux и macOS.** Шаг установки
  live-runtime в CI был только для Windows, поэтому `collect_live_capture_deps()`
  печатал `[skip] optional live capture package sounddevice` и продолжал сборку.
  Бинарник уезжал в релиз без модуля вообще. Затрагивало `GigaAMTranscriber-linux-x64`
  (portable и offline) и полную macOS `.app`, где вдобавок не было PyObjC
  ScreenCaptureKit для системного звука.
- **Файлов `requirements-live-linux.txt` и `requirements-live-macos.txt` не
  существовало.** На них ссылались README, README_EN, `capture_capabilities()` и
  тексты ошибок в рантайме — совет из сообщения об ошибке был невыполним. Теперь
  файлы есть; macOS-набор включает `pyobjc-framework-AVFoundation` и
  `pyobjc-framework-ScreenCaptureKit`, которые нужны системному захвату.
- **PortAudio не приезжал вместе с Linux-колесом sounddevice.** На PyPI для Linux
  лежит только чистопитоновое `py3-none-any` колесо: `_sounddevice_data` с
  библиотекой там нет, а загрузчик ищет PortAudio через `ctypes.util.find_library`
  и на Linux не имеет запасного пути. Внутри PyInstaller-бандла `find_library`
  смотрит в кэш `ldconfig`, а не в `sys._MEIPASS`, так что даже вшитая копия не
  находилась бы. Теперь спек вшивает `libportaudio.so.2`, а `src/live/capture/linux.py`
  на время импорта подставляет sounddevice путь к ней. Установка системного
  `libportaudio2` для готовых сборок больше не нужна.
- **`collect_all('sounddevice')` не забирал `_sounddevice_data`.** Это отдельный
  top-level пакет, и именно в нём на macOS лежит `libportaudio.dylib`, который
  использует запасная ветка загрузчика. Он и cffi-обвязка `_sounddevice` теперь
  собираются явно.
- **Тест «отсутствующий runtime даёт инструкцию» ничего не проверял.** Он
  подменял `api_loader`, минуя `_load_linux_api`, поэтому текст сообщения не
  участвовал в проверке — и не заметил, что тот ссылается на несуществующий файл.

### Изменено

- Сообщение об ошибке на Linux теперь называет и пакет `sounddevice`, и системный
  `libportaudio2`, а не только файл требований.
- `--selfcheck`, который CI гоняет на **собранном** бинарнике перед публикацией,
  проверяет и модули live-захвата. Появился отдельный флаг `--live-capture-smoke`;
  его вызывает `scripts/verify_macos_bundle.py`, потому что полная `.app`
  собирается другим job'ом и `--selfcheck` не запускает. Молчаливый `[skip]`
  больше не может доехать до релиза — так же, как это было закрыто для issue #19.
- `packaging/build_exe_mac.sh` падает заранее, если live-зависимости не
  установлены, вместо тихой сборки без захвата.

## English

### Fixed

- **Live capture was missing from the Linux and macOS builds.** The CI step that
  installs the live runtime existed only for Windows, so `collect_live_capture_deps()`
  printed `[skip] optional live capture package sounddevice` and carried on. The
  binary shipped without the module at all. This affected `GigaAMTranscriber-linux-x64`
  (portable and offline) and the full macOS `.app`, which additionally lacked
  PyObjC ScreenCaptureKit for system audio.
- **`requirements-live-linux.txt` and `requirements-live-macos.txt` did not
  exist.** README, README_EN, `capture_capabilities()` and the runtime error
  messages all pointed at them, so the advice the user was given was impossible
  to follow. Both files now exist; the macOS set includes
  `pyobjc-framework-AVFoundation` and `pyobjc-framework-ScreenCaptureKit`, which
  system capture needs.
- **PortAudio does not ship with the Linux sounddevice wheel.** PyPI only has the
  pure-Python `py3-none-any` wheel for Linux: no `_sounddevice_data`, and the
  loader resolves PortAudio through `ctypes.util.find_library` with no fallback
  on Linux. Inside a PyInstaller bundle `find_library` consults the `ldconfig`
  cache rather than `sys._MEIPASS`, so even a bundled copy would not be found.
  The spec now bundles `libportaudio.so.2` and `src/live/capture/linux.py` points
  sounddevice at it for the duration of the import. Released builds no longer
  need the system `libportaudio2`.
- **`collect_all('sounddevice')` did not pick up `_sounddevice_data`.** That is a
  separate top-level package, and on macOS it holds the `libportaudio.dylib` the
  loader's fallback branch uses. It and the `_sounddevice` cffi shim are now
  collected explicitly.
- **The "missing runtime gives guidance" test asserted nothing.** It replaced
  `api_loader`, bypassing `_load_linux_api`, so the message itself was never
  exercised — and nobody noticed it named a file that did not exist.

### Changed

- The Linux error message now names both the `sounddevice` package and the system
  `libportaudio2`, not just the requirements file.
- `--selfcheck`, which CI runs against the **built** binary before publishing,
  now covers the live capture modules as well. A dedicated `--live-capture-smoke`
  flag was added and is invoked by `scripts/verify_macos_bundle.py`, since the
  full `.app` is built by a separate job that does not run `--selfcheck`. A silent
  `[skip]` can no longer reach a release — the same gate that closed issue #19.
- `packaging/build_exe_mac.sh` now fails up front when the live dependencies are
  missing, instead of quietly building without capture.
