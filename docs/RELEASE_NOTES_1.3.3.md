# GigaAM Transcriber 1.3.3

Версия 1.3.3 исправляет подтверждённое падение portable Sortformer ONNX из
[issue #34](https://github.com/dubr1k/GigaAMGUI/issues/34).

Scope релиза ограничен mapping-регрессией. Сообщения из того же issue о загрузке
PyTorch backend и фактическом использовании CUDA требуют отдельной проверки на
Windows-машине репортёра; issue остаётся открытым с меткой `test` до подтверждения.

## Что исправлено

- Sortformer ONNX теперь использует общий механизм сопоставления распознанных слов
  с найденными speaker-сегментами. В 1.3.2 сама ONNX-модель успешно выполняла
  inference, но следующий этап падал с ошибкой
  `SortformerOnnxDiarizationManager has no attribute map_speakers_to_transcription`.
- Ошибка диаризации больше не сопровождается советами о Hugging Face token и
  gated-моделях, если выбран ONNX/Sortformer. Эти советы относятся только к
  pyannote.
- Release smoke встроенного Sortformer расширен: он проверяет не только запуск
  ONNX inference и фактическую provider chain, но и тот самый mapping-контракт,
  который был пропущен в 1.3.2.

## Об ускорении ONNX

Windows/Linux portable по-прежнему поставляется с `onnxruntime-gpu`. При успешной
инициализации CUDA фактическая provider chain должна начинаться с
`CUDAExecutionProvider`; если она начинается с `CPUExecutionProvider`, ускорение
не задействовано. Высокая загрузка CPU сама по себе не доказывает fallback:
подготовка признаков, VAD и декодирование выполняют часть работы на CPU, а
диспетчер задач Windows по умолчанию часто показывает график `3D`, а не
`CUDA/Compute`. Источником истины является строка `session_providers` в
журнале/smoke-отчёте. v1.3.3 не заявляет отдельного исправления CUDA runtime.

## Проверка

- Добавлен регрессионный тест полного mapping-контракта Sortformer ONNX.
- Built-binary smoke выполняет ONNX inference и mapping тестового слова к
  `Спикер №1`.
- Сохранены проверки portable/offline-сборок, macOS bundle version и provider
  policy.

Подробности реализации находятся в
[`docs/CHANGELOG.md`](https://github.com/dubr1k/GigaAMGUI/blob/main/docs/CHANGELOG.md).
