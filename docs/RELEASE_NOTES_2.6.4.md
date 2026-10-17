# GigaAM Transcriber 2.6.4

Патч-релиз: понятная ошибка диаризации вместо ложного «pyannote.audio не установлен» и устойчивая сборка релизов.

## Исправлено

- **«pyannote.audio не установлен», хотя пакет стоит** ([#72](https://github.com/dubr1k/GigaAMGUI/issues/72)). Любая ошибка импорта `torch` или `pyannote.audio` показывалась этим текстом, и настоящая причина терялась по дороге к окну. Если pyannote установлен, но не загружается одна из его зависимостей (на Windows обычно DLL), переустановка пакета ничего не меняла. Теперь «не установлен» пишется, только когда не найден сам пакет; иначе окно называет настоящую ошибку импорта, а полный трейсбек попадает в лог. То же для NVIDIA Sortformer.
- **Подсказка по установке pyannote больше не ломает окружение.** Совет `pip install pyannote.audio` без версии ставил pyannote.audio 4.x вместе с torch 2.9+ и torchcodec — мимо версий, с которыми работает приложение. Подсказка и комментарии в `requirements.txt` теперь ведут на `pip install -r requirements.txt`.

## Уточнение к 2.6.3

Новые имена при совпадении (`meeting_1.txt`, `meeting_2.txt`) работают не только в Liquid, как сказано в заметках 2.6.3, а во всех интерфейсах: PyQt, web, CLI и TUI. Повторная обработка того же файла в ту же папку теперь кладёт новый результат рядом, а не поверх прежнего.

## Для разработчиков

- ffmpeg для Windows и Linux закреплён на месячной сборке BtbN (`autobuild-2026-09-30-13-08`): дневные сборки удаляются примерно через две недели, и пин 2.6.3 на `autobuild-2026-10-05` сломал бы следующий релиз. ffmpeg/ffprobe для Intel-сборки macOS теперь тоже сверяются по SHA256. Правило проверяет тест в `test_release_hardening.py`.
- `scripts/install_liquid_local.sh` — локальная сборка Liquid и worker с теми же проверками версий и smoke-тестами, что в CI, с установкой в `/Applications`.

## Что проверить

- Windows, запуск из исходников, включённая диаризация pyannote: если зависимость pyannote не загружается, окно «Processing stopped» должно назвать конкретную ошибку импорта, а не «pyannote.audio не установлен».
- Обычная транскрибация с диаризацией на любой платформе работает как в 2.6.3.

PyQt, web, TUI, Liquid и протокол worker в остальном не менялись.

---

## English

Patch release: a meaningful diarization error instead of a false “pyannote.audio is not installed”, and a release build that no longer depends on short-lived downloads.

### Fixed

- **“pyannote.audio не установлен” although the package is installed** ([#72](https://github.com/dubr1k/GigaAMGUI/issues/72)). Every import failure of `torch` or `pyannote.audio` was reported with this text, and the real cause was lost on its way to the dialog. When pyannote is installed but one of its dependencies fails to load (on Windows usually a DLL), reinstalling the package changed nothing. The dialog now says “not installed” only when the package itself is missing; otherwise it names the actual import error, and the full traceback goes to the log. The same applies to NVIDIA Sortformer.
- **The pyannote install hint no longer breaks the environment.** The advice `pip install pyannote.audio` without a version installed pyannote.audio 4.x together with torch 2.9+ and torchcodec, outside the versions the app works with. The hint and the comments in `requirements.txt` now point to `pip install -r requirements.txt`.

### Clarification for 2.6.3

Collision-free export names (`meeting_1.txt`, `meeting_2.txt`) apply not only to Liquid, as the 2.6.3 notes said, but to every front end: PyQt, web, CLI and TUI. Processing the same file into the same folder again now writes the new result next to the previous one instead of over it.

### For developers

- Windows and Linux ffmpeg is pinned to a month-end BtbN build (`autobuild-2026-09-30-13-08`): daily builds are deleted after about two weeks, and the 2.6.3 pin on `autobuild-2026-10-05` would have broken the next release. The Intel macOS ffmpeg/ffprobe binaries are now verified by SHA256 as well. A test in `test_release_hardening.py` enforces both rules.
- `scripts/install_liquid_local.sh` builds Liquid and the worker locally with the same version checks and smoke tests as CI and installs the result into `/Applications`.

### What to check

- Windows, running from source, pyannote diarization enabled: if a pyannote dependency fails to load, the “Processing stopped” dialog must name the specific import error rather than “pyannote.audio не установлен”.
- Regular transcription with diarization works as in 2.6.3 on every platform.

PyQt, web, the TUI, Liquid and the worker protocol are otherwise unchanged.
