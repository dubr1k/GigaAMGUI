# GigaAM Transcriber 1.6.0

Релиз по двум issue: [#45](https://github.com/dubr1k/GigaAMGUI/issues/45) —
сборка под macOS x86_64 (Intel), и [#46](https://github.com/dubr1k/GigaAMGUI/issues/46) —
диаризация падала в офлайн-сборке на любом бэкенде.

## Русский

### Добавлено

- **Сборка macOS x86_64 (Intel):
  `GigaAMTranscriber-macos-x86_64-app-offline-<тег>.zip` (#45).** До этого
  релиза у владельца Intel-мака не было ни одного рабочего пути: готовые
  arm64-ассеты на x86_64 не стартуют в принципе (Rosetta переводит в обратную
  сторону), а установка из исходников упирается в `torch>=2.6.0` при том, что
  колёса PyTorch под macOS x86_64 закончились на 2.2.2. Понизить пин нельзя:
  `pyannote.audio` 3.1.1 завязан на legacy-API `torchaudio`, а Blackwell
  требует ветку 2.8.

  Поэтому Intel-вариант собирается без torch и без mlx. Вместе с torch отпадают
  `pyannote.audio`, `lightning`, `speechbrain` и `accelerate`: распознавание,
  VAD и диаризация целиком идут через `onnx-asr` и ONNX Runtime, где на x86_64
  доступны провайдеры CoreML и CPU. Публикуется офлайн-вариант — именно модели
  рядом с `.app` позволяют сборке работать без torch и без токена HuggingFace.

  **Требуется macOS 13 и новее** — ограничение колёс `onnxruntime` под x86_64.
  Оно же записано в `LSMinimumSystemVersion` бандла.

  Arm64-пайплайн не изменился.

- **Флаг `--onnx-runtime-smoke`** — аналог MLX-смока для сборок без torch:
  поднимает нативный ONNX Runtime и печатает доступные провайдеры, не скачивая
  веса. Нужен отдельно от `--offline-models-smoke`, который требует уже
  привезённых моделей.

### Исправлено

- **Диаризация падала на любом бэкенде (#46).** Симптом: `ValueError:
  Diarization output overlap between adjacent ASR chunks`, транскрипт сохранялся
  без разметки спикеров, переключение Pyannote ↔ ONNX не помогало.

  Ломалась не диаризация, а таймлайн ASR, который ей подают на вход —
  `map_speakers_to_transcription` только первой это замечала. Цепочка целиком:

  1. В офлайн-сборке ASR по умолчанию — ONNX (`_DEFAULT_ASR_BACKEND` в
     `src/config.py`), а ONNX-путь режет речь на области через silero из
     `onnx-asr`.
  2. `BaseVad._merge_segments` в `onnx-asr` при разрезании речи длиннее
     `max_speech_duration_s` (20 с по умолчанию) расширяет каждый кусок на
     `speech_pad` (30 мс) в обе стороны, а шаг делает ровно на
     `max_speech_duration`. Соседние области выходят перекрытыми на 2·pad = 60
     мс. На 60 секундах непрерывной речи это буквально `(0.000, 19.970)`,
     `(19.910, 39.910)`, `(39.850, 59.850)`.
  3. `merge_speech_regions` (`src/core/asr/vad.py`) пропускала перекрытие
     насквозь: закрывая область, она брала начало следующей как есть, не глядя
     на конец только что выданной.
  4. `plan_audio_chunks` (`src/core/asr/chunking.py`) превращает границы
     областей в номинальные окна один в один, поэтому окно N+1 стартовало на 60
     мс раньше, чем заканчивалось окно N.
  5. `normalize_chunk_words` подрезает слова в **своё** окно: последнее слово
     чанка N кончалось на 19.970, первое слово чанка N+1 начиналось на 19.910.
  6. `_append_mapped_turns` держит строгий контракт монотонности и на
     `start < previous_end` бросает исключение, а `_apply_diarization`
     сознательно не маскирует сбой фиктивным «Спикер №1».

  Условий для срабатывания было два: офлайн-сборка (ONNX ASR) **и** непрерывная
  речь длиннее 20 с. На обычной сборке `auto` уходит в PyTorch с pyannote-VAD, а
  `Annotation.get_timeline().support()` по определению не перекрывается — отсюда
  и то, что баг видели не все.

  Починены оба места, где перекрытие проходило дальше: `merge_speech_regions`
  больше не начинает область раньше конца уже выданной, а `plan_audio_chunks`
  ведёт сквозной таймлайн и поджимает к нему любую область, начинающуюся раньше.
  Через планировщик идут все три бэкенда, так что монотонность держится
  независимо от того, что вернёт конкретный VAD.

  Проверку в `mapping.py` намеренно не ослабляли: подрезка вместо исключения
  «починила» бы диаризацию, но оставила бы перекрытые таймкоды — реплики в
  SRT/VTT наезжали бы друг на друга на стыках 20-секундных окон, а следующий баг
  того же рода прошёл бы уже молча. Побочно этим же фиксом уходит и наезд
  субтитров.

### Изменено

- `auto` на macOS x86_64 выбирает ONNX: PyTorch-ветка там не медленнее, а
  мертва. Замороженная не-arm64 macOS-сборка ставит `onnx` дефолтом и для ASR, и
  для диаризации — иначе пользователь получал бы необъяснимое
  `No module named 'gigaam'`.
- Версия бандлов живёт в `APP_VERSION` (`packaging/_spec_common.py`) — один
  источник правды на оба macOS-спека.

### Тесты

- `test_merge_speech_regions_clips_overlap_from_padded_vad_output` — на
  настоящих числах silero; `test_merge_speech_regions_never_returns_overlapping_boundaries`.
- `test_overlapping_vad_regions_do_not_produce_overlapping_nominal_spans` —
  планировщик держит монотонность даже на заведомо перекрытом входе.
- `test_issue_46_padded_vad_regions_do_not_break_diarization` — сквозной прогон
  VAD → планировщик → слова → маппинг на обоих менеджерах; до правки падал ровно
  исходным `ValueError`.
- `test_onnx_pipeline_imports_without_the_torch_chain` — в подпроцессе блокирует
  всю torch-цепочку в `sys.meta_path` и импортирует ONNX-путь вместе с
  `src.gui.app_qt`. Достаточно одного `import torch` на уровне модуля в
  `src/gui` или `src/core`, и Intel-сборка перестанет стартовать; на
  Apple Silicon это ничем другим не видно.
- `tests/test_macos_x86_64_packaging.py` целиком: набор зависимостей без
  torch-цепочки, `target_arch`, `LSMinimumSystemVersion`, единый источник
  версии, гейты build-скрипта и CI.
- `test_offline_bundle_switches_default_backends_to_onnx` переписан со сравнения
  текста `config.py` на проверку поведения.

### Что проверено, а что нет

Intel-сборка **не запускалась на живом Intel-маке** ни автором правки, ни CI до
этого тега. Проверено то, что проверяемо со стороны: набор зависимостей —
резолвером pip под `macosx_13_0_x86_64` (48 колёс, ничего из torch-цепочки),
граф импортов — тестом с блокировкой torch, наличие x86_64 у всех Mach-O и
отсутствие torch внутри `.app` — гейтом в `verify_macos_bundle.py`, доступность
моделей и дефолтные бэкенды — `--offline-models-smoke` на собранном бандле.

Диагноз тупика под Intel, разбор альтернатив и локальная проверка на реальном
железе (macOS 26.7, Core i5-1038NG7) — @ARTSPRN. Отчёт с трейсбеком по
диаризации — @nilfheiz-dev.

## English

### Added

- **macOS x86_64 (Intel) build:
  `GigaAMTranscriber-macos-x86_64-app-offline-<tag>.zip` (#45).** Intel Macs had
  no working path at all: arm64 assets cannot start on x86_64, and installing
  from source hits `torch>=2.6.0` while macOS x86_64 PyTorch wheels stopped at
  2.2.2. The Intel bundle therefore ships without torch and without mlx —
  recognition, VAD and diarization all run through `onnx-asr` and ONNX Runtime
  (CoreML and CPU). Only the offline variant is published, since the models next
  to the `.app` are what let it work without torch or a HuggingFace token.
  **Requires macOS 13 or newer** (an `onnxruntime` x86_64 wheel constraint). The
  arm64 pipeline is unchanged.
- `--onnx-runtime-smoke`: a weights-free native ONNX Runtime gate for builds
  without torch.

### Fixed

- **Diarization failed on every backend in offline builds (#46).** The fault was
  in the ASR timeline, not in diarization, which is why switching Pyannote ↔ ONNX
  changed nothing. `onnx-asr`'s silero VAD splits speech longer than
  `max_speech_duration_s` (20 s) and pads each piece by `speech_pad` (30 ms) on
  both sides while stepping by exactly `max_speech_duration`, so adjacent regions
  overlap by 60 ms. `merge_speech_regions` passed that overlap through,
  `plan_audio_chunks` turned it into overlapping nominal windows, and the strict
  monotonic contract in `_append_mapped_turns` correctly refused the result —
  leaving the transcript without speaker labels. Both places now keep the
  timeline monotonic regardless of what a VAD returns. The same fix removes
  overlapping SRT/VTT cues at 20-second window seams.

### Changed

- `auto` resolves to ONNX on macOS x86_64, and a frozen non-arm64 macOS build
  defaults both ASR and diarization to `onnx` instead of failing with
  `No module named 'gigaam'`.

### Verification status

The Intel bundle has not yet been launched on real Intel hardware. Everything
verifiable off-target was checked: dependency resolution through pip against
`macosx_13_0_x86_64`, a torch-free import graph, the x86_64/no-torch bundle gate,
and `--offline-models-smoke` on the built app.

Intel analysis and local hardware verification by @ARTSPRN; the diarization
report with a full traceback by @nilfheiz-dev.
