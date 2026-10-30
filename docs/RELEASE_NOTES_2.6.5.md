# GigaAM Transcriber 2.6.5

Патч-релиз: подготовка Python runtime для выбранных компонентов диаризации до начала пакетной обработки в портативном приложении.

## Исправлено

- **`No module named torch` при запуске pyannote из портативного EXE.** Портативный EXE не включает `torch`. При старте приложения с ONNX или MLX ASR `torch` может не потребоваться; если позднее включить pyannote, ему нужен `torch`, которого в таком EXE нет. Теперь GUI до запуска пакета обработки проверяет, подготавливает и активирует управляемый приложением runtime для выбранных компонентов. Отмена подготовки оставляет GUI доступным, а обработка только с ONNX не запускает загрузку runtime.
- `pip install` в исходном Python venv не изменяет портативный EXE. Его зависимости устанавливаются в runtime, которым управляет само приложение.

## Для разработчиков

- Frozen selfcheck проверяет импорты после установки runtime.
- В pytest-гейт workflow добавлен `tests/test_gui_processing_runs.py` для регрессий GUI-пути запуска обработки.

## Что проверить

- Портативный EXE: ONNX ASR без pyannote не должен загружать runtime.
- Портативный EXE: включённый pyannote должен подготовить и активировать runtime до старта пакета; отмена подготовки не должна ломать GUI.
- Запуск из исходников использует свой Python environment; установка пакетов через pip там не устанавливает их в runtime портативного EXE.

---

## English

Patch release: prepare the Python runtime for selected diarization components before batch processing starts in the portable app.

### Fixed

- **`No module named torch` when starting pyannote from the portable EXE.** The portable EXE does not bundle `torch`. With ONNX or MLX ASR, startup may not need `torch`; enabling pyannote later does, but that EXE has no bundled `torch`. The GUI now checks, prepares and activates the app-managed runtime for the selected components before starting a batch. Cancelling preparation leaves the GUI usable, and ONNX-only processing does not start a runtime download.
- `pip install` in the source Python venv does not modify the portable EXE. Its dependencies are installed into the runtime managed by the app.

### For developers

- The frozen selfcheck verifies imports after runtime installation.
- The build workflow's pytest gate now includes `tests/test_gui_processing_runs.py` for GUI processing-start regressions.

### What to check

- Portable EXE: ONNX ASR without pyannote should not download the runtime.
- Portable EXE: enabled pyannote should prepare and activate the runtime before a batch starts; cancelling preparation should leave the GUI usable.
- Running from source uses its own Python environment; installing packages there with pip does not install them into the portable EXE's runtime.

These notes describe the change and expected checks; they do not claim a reporter-confirmed resolution or completed Windows verification.
