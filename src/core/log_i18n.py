"""Английский перевод строк журнала обработки.

Журнал пишется по-русски у источника (processor, model_loader, ASR-бэкенды,
audio_converter, tui_worker). Клиенты с английским интерфейсом переводят его
построчно: PyQt через ``translate_log_line``, GigaAMLiquid — той же таблицей,
скопированной в ``LogTranslation.swift`` (тест сверяет оба списка).

Правила — пары «регулярное выражение → шаблон с группами``\\1``». Порядок
важен: первое совпадение побеждает. Строки, не подошедшие ни к одному правилу,
возвращаются как есть.
"""

from __future__ import annotations

import re

LOG_TRANSLATIONS: tuple[tuple[str, str], ...] = (
    # Модель и движок
    (r"^Загружаем модель распознавания речи…$", r"Loading the speech recognition model…"),
    (r"^Подготавливаем модель распознавания речи \(GigaAM-v3\)…$", r"Preparing the speech recognition model (GigaAM-v3)…"),
    (r"^Загружаем модель для Apple Silicon \(MLX\): (.+)$", r"Loading the Apple Silicon (MLX) model: \1"),
    (r"^При первом запуске модель скачивается — это может занять несколько минут\.$", r"The model is downloaded on first launch; this can take a few minutes."),
    (r"^Движок распознавания: (.+)$", r"Recognition engine: \1"),
    (r"^Вычисления выполняются на устройстве: (.+)$", r"Computing on: \1"),
    (r"^Модель готова\.$", r"Model ready."),
    (r"^Модель готова \(ONNX, (.+?)\); вычисления выполняются на: (.+)$", r"Model ready (ONNX, \1); computing on: \2"),
    (r"^Модель загружена\.$", r"Model loaded."),
    (r"^Не удалось загрузить модель распознавания \(ONNX\):(.*)$", r"Could not load the speech recognition model (ONNX):\1"),
    (r"^Не удалось загрузить модель распознавания:(.*)$", r"Could not load the speech recognition model:\1"),
    (r"^Не удалось загрузить модель распознавания$", r"Could not load the speech recognition model"),
    (r"^Не удалось загрузить модель MLX \((.+?)\): (.+)$", r"Could not load the MLX model (\1): \2"),
    (r"^Не удалось загрузить модель через движок (.+)$", r"Could not load the model with the \1 engine"),
    (r"^Не удалось запустить движок распознавания: (.+)$", r"Could not start the recognition engine: \1"),
    (r"^Не удалось выбрать движок распознавания для загрузки модели$", r"Could not pick a recognition engine to load the model"),
    # Файл
    (r"^Файл (\d+) из (\d+): (.+)$", r"File \1 of \2: \3"),
    (r"^Длительность записи: неизвестна$", r"Recording length: unknown"),
    (r"^Длительность записи: (.+)$", r"Recording length: \1"),
    (r"^Файл: (.+)$", r"File: \1"),
    (r"^Файл пропущен: (.+)$", r"File skipped: \1"),
    # Подготовка звука
    (r"^Подготавливаем звук: (.+) → WAV 16 кГц…$", r"Preparing audio: \1 → 16 kHz WAV…"),
    (r"^Звук подготовлен\.$", r"Audio prepared."),
    (r"^Подготовка звука заняла слишком долго и была прервана — файл не обработан\.$", r"Audio preparation took too long and was stopped; the file was not processed."),
    (r"^FFmpeg не смог подготовить звук \(код ошибки (\d+)\)\. Подробности ниже:$", r"FFmpeg could not prepare the audio (error code \1). Details below:"),
    (r"^  FFmpeg: (.*)$", r"  FFmpeg: \1"),
    (r"^Возможная причина: файл повреждён или загружен не до конца.*$", r"Likely cause: the file is damaged or was not fully downloaded (MP4 keeps its «moov» metadata at the end; a truncated file cannot be read by FFmpeg)."),
    (r"^Что попробовать: перезаписать/скачать файл заново.*$", r"What to try: re-download or re-save the file, open it in another player and export it again, or use a different file."),
    (r"^Ошибка: не найдена программа FFmpeg .*$", r"Error: FFmpeg was not found (neither in the app nor on the system); audio cannot be prepared without it."),
    (r"^Ошибка: не удалось запустить FFmpeg \((.+)\)\.$", r"Error: could not start FFmpeg (\1)."),
    (r"^Проверьте, что рядом с приложением нет несовместимого ffmpeg.*$", r"Make sure there is no incompatible ffmpeg for another OS/architecture next to the app."),
    (r"^Ошибка: файл не найден: (.+)$", r"Error: file not found: \1"),
    (r"^Ошибка: это не файл, а папка или другой объект: (.+)$", r"Error: not a file (a folder or another object): \1"),
    # Очистка звука
    (r"^Очистка звука: (.+) \(режим (.+)\)$", r"Audio cleanup: \1 (mode \2)"),
    (r"^Очистка звука не выполнялась: (.+) \(режим (.+)\)$", r"Audio cleanup skipped: \1 (mode \2)"),
    (r"^  Почему: (.+)$", r"  Why: \1"),
    (r"^  Очистка не применена: (.+)$", r"  Cleanup not applied: \1"),
    (r"^Очистка звука недоступна, используем запись как есть: (.+)$", r"Audio cleanup unavailable; using the recording as is: \1"),
    (r"^не требуется$", r"not needed"),
    (r"^лёгкая очистка от шума$", r"light noise cleanup"),
    (r"^нейросетевое подавление шума$", r"neural noise suppression"),
    (r"^выравнивание громкости$", r"loudness normalisation"),
    (r"^речь слабо выделяется на фоне шума$", r"speech barely stands out from the noise"),
    (r"^в записи постоянный фоновый шум$", r"constant background noise in the recording"),
    (r"^в записи низкочастотный гул$", r"low-frequency rumble in the recording"),
    (r"^в записи сильный фоновый шум$", r"heavy background noise in the recording"),
    (r"^нейросетевая очистка недоступна, применена мягкая очистка FFmpeg$", r"neural cleanup unavailable; gentle FFmpeg cleanup applied"),
    (r"^запись сильно перегружена \(клиппинг\), очистка не поможет$", r"the recording is heavily clipped; cleanup would not help"),
    (r"^в записи почти нет речи, очистка не выполнялась$", r"almost no speech in the recording; cleanup skipped"),
    (r"^запись тихая, но усиливать её небезопасно$", r"the recording is quiet, but amplifying it is unsafe"),
    (r"^речь тихая, но чистая — громкость выровнена$", r"speech is quiet but clean; loudness normalised"),
    (r"^запись чистая, очистка не нужна$", r"the recording is clean; no cleanup needed"),
    (r"^очистка звука отключена в настройках$", r"audio cleanup is disabled in settings"),
    (r"^лёгкая очистка выбрана в настройках$", r"light cleanup selected in settings"),
    (r"^нейросетевая очистка выбрана в настройках$", r"neural cleanup selected in settings"),
    (r"^средство очистки завершилось с ошибкой, использована исходная запись$", r"the cleanup tool failed; the original recording was used"),
    (r"^результат очистки отклонён: (.+)$", r"cleanup result rejected: \1"),
    (r"^не удалось проверить результат очистки: (.+)$", r"could not verify the cleanup result: \1"),
    (r"^после очистки появились перегрузки$", r"clipping appeared after cleanup"),
    (r"^очистка заглушила часть речи$", r"cleanup silenced part of the speech"),
    (r"^после очистки речь стала слишком тихой$", r"speech became too quiet after cleanup"),
    (r"^громкость ушла от нужного уровня$", r"loudness drifted from the target"),
    (r"^очистка не дала заметного улучшения$", r"cleanup brought no measurable improvement"),
    # Распознавание
    (r"^Распознаём речь…$", r"Recognizing speech…"),
    (r"^Речь найдена: участков — (\d+), фрагментов для распознавания — (\d+)$", r"Speech found: \1 region(s), \2 fragment(s) to recognize"),
    (r"^Распознавание завершено: фрагментов текста — (\d+)$", r"Recognition finished: \1 text fragment(s)"),
    (r"^Не удалось найти речь в записи \(ошибка детектора речи\): (.+)$", r"Could not find speech in the recording (speech detector error): \1"),
    (r"^Ошибка при распознавании речи: (.+)$", r"Speech recognition error: \1"),
    (r"^Реплик в тексте: (\d+)$", r"Utterances in the text: \1"),
    (r"^Внимание: фрагмент (.+) распознан без текста$", r"Warning: fragment \1 was recognized without text"),
    (r"^Внимание: речь не распознана — все фрагменты пустые$", r"Warning: no speech recognized; every fragment is empty"),
    (r"^Внимание: распознанный текст пуст$", r"Warning: the recognized text is empty"),
    (r"^Внимание: в файле (.+) не найдено речи\.$", r"Warning: no speech found in \1."),
    (r"^Внимание: говорящих определить не удалось, файл (.+) не создан$", r"Warning: speakers could not be identified; \1 was not created"),
    (r"^Возможные причины:$", r"Possible reasons:"),
    (r"^  1\. В записи нет речи или она очень тихая$", r"  1. The recording has no speech, or it is very quiet"),
    (r"^  2\. Не работает токен HuggingFace для pyannote/segmentation-3\.0$", r"  2. The HuggingFace token for pyannote/segmentation-3.0 does not work"),
    (r"^  3\. Не удалось разбить запись на участки речи$", r"  3. The recording could not be split into speech regions"),
    (r"^Проверьте токен HF_TOKEN и убедитесь, что приняли условия доступа:$", r"Check the HF_TOKEN and make sure you accepted the access terms:"),
    (r"^Проверьте токен HF_TOKEN в \.env файле и убедитесь, что приняли условия доступа:$", r"Check the HF_TOKEN in the .env file and make sure you accepted the access terms:"),
    (r"^Внимание: определение говорящих переключилось на запасной режим — (.+)$", r"Warning: speaker identification switched to a fallback mode — \1"),
    (r"^Внимание: (.+)$", r"Warning: \1"),
    # Запасные режимы разбиения (ASR-бэкенды) — приходят внутри «Внимание: …»
    (r"^VAD пропустил длинный участок с активным звуком; использовано полное разбиение по тихим точкам с перекрытием$", r"VAD skipped a long stretch of active audio; full silence-based splitting with overlap was used"),
    (r"^VAD недоступен \((.+?)\): (?:проверьте локальный кэш или HF_TOKEN и доступ к pyannote/segmentation-3\.0; )?использовано резервное разбиение по тихим точкам с перекрытием$", r"VAD unavailable (\1): check the local cache or HF_TOKEN and access to pyannote/segmentation-3.0; fallback silence-based splitting with overlap was used"),
    (r"^ONNX VAD недоступен \((.+?)\); использовано разбиение по тихим точкам с перекрытием$", r"ONNX VAD unavailable (\1); silence-based splitting with overlap was used"),
    (r"^VAD отключён настройкой ASR_SEGMENTATION_MODE: использовано разбиение по тихим точкам с перекрытием$", r"VAD disabled by ASR_SEGMENTATION_MODE: silence-based splitting with overlap was used"),
    (r"^VAD отключён настройкой ASR_SEGMENTATION_MODE: использовано legacy-разбиение по 20 секунд без перекрытия$", r"VAD disabled by ASR_SEGMENTATION_MODE: legacy 20-second splitting without overlap was used"),
    (r"^VAD отключён настройкой: использовано разбиение по тихим точкам с перекрытием$", r"VAD disabled in settings: silence-based splitting with overlap was used"),
    (r"^VAD отключён настройкой: использовано разбиение по 20 секунд$", r"VAD disabled in settings: 20-second splitting was used"),
    (r"^(.+?) завершил inference с ошибкой (.+?); транскрибация начата заново на CPUExecutionProvider, прогресс отсчитывается с нуля$", r"\1 failed during inference (\2); recognition restarted on CPUExecutionProvider, progress starts over"),
    (r"^Аварийный fallback: (.+?) не загрузился, использован PyTorch backend$", r"Emergency fallback: \1 failed to load; the PyTorch engine was used"),
    # Говорящие
    (r"^Определяем, кто говорит \((.+)\)…$", r"Identifying speakers (\1)…"),
    (r"^Определение говорящих выполняется на: (.+)$", r"Speaker identification runs on: \1"),
    (r"^устройство (.+?), провайдер (.+)$", r"device \1, provider \2"),
    (r"^устройство (.+)$", r"device \1"),
    (r"^провайдер (.+)$", r"provider \1"),
    (r"^Говорящих найдено: (\d+)$", r"Speakers found: \1"),
    (r"^Не удалось определить говорящих, текст сохранён без разметки по говорящим: (.+)$", r"Could not identify speakers; the text was saved without speaker labels: \1"),
    (r"^Не удалось подготовить определение говорящих: (.+)$", r"Could not set up speaker identification: \1"),
    (r"^Ошибка при определении говорящих: (.+)$", r"Speaker identification error: \1"),
    (r"^Частая причина: на huggingface\.co не приняты условия ВСЕХ моделей —$", r"Common cause: the terms of ALL models were not accepted on huggingface.co —"),
    (r"^либо у токена нет права read\.$", r"or the token lacks read permission."),
    (r"^Причина указана выше\.$", r"The reason is above."),
    (r"^Укажите токен в настройках диаризации \(определения говорящих\)\.$", r"Set the token in the diarization (speaker identification) settings."),
    (r"^Ошибка: (.+)$", r"Error: \1"),
    # Результат
    (r"^Объём текста: (\d+) символов$", r"Text size: \1 characters"),
    (r"^Сохранён файл: (.+)$", r"Saved file: \1"),
    (r"^Готово за (.+?) \(подготовка звука (.+?) с, распознавание (.+?) с\)$", r"Done in \1 (audio preparation \2 s, recognition \3 s)"),
    # Длительности из TimeFormatter.format_duration
    (r"^(\d+) ч (\d+) мин$", r"\1 h \2 min"),
    (r"^(\d+) мин (\d+) сек$", r"\1 min \2 s"),
    (r"^(\d+) сек$", r"\1 s"),
    (r"^Не удалось обработать (.+?): (.+)$", r"Could not process \1: \2"),
    (r"^Остановка запрошена: закончим текущий файл и остановимся\.$", r"Stop requested: finishing the current file, then stopping."),
)

_COMPILED = tuple((re.compile(pattern), template) for pattern, template in LOG_TRANSLATIONS)


def translate_log_line(line: str) -> str:
    """Перевести одну строку журнала на английский; незнакомая строка — как есть.

    Захваченные группы переводятся рекурсивно: «  Почему: <причина>» даёт
    «  Why: <reason>», а имя файла или текст исключения остаются как есть.
    """
    for pattern, template in _COMPILED:
        match = pattern.search(line)
        if match is None:
            continue
        groups = [translate_log_line(group) if group else "" for group in match.groups()]
        return _fill_template(template, groups)
    return line


def _fill_template(template: str, groups: list[str]) -> str:
    return re.sub(r"\\(\d)", lambda m: groups[int(m.group(1)) - 1], template)


def translate_log(text: str) -> str:
    """Построчный перевод многострочного сообщения."""
    return "\n".join(translate_log_line(line) for line in text.split("\n"))
