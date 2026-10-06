import Foundation

/// Человекочитаемые названия стадий обработки из событий `progress` воркера.
/// Идентификаторы и тексты совпадают с `_STAGE_NAMES` в PyQt
/// (`src/gui/processing_mixin.py`) и с `ProgressStage` в `src/core/progress.py`:
/// без этой таблицы Liquid показывал сырой id («preprocessing») под прогрессом.
public enum StageLabel {
    public static let stages: [String: (ru: String, en: String)] = [
        "preparing": ("Подготовка…", "Preparing…"),
        "conversion": ("Конвертация…", "Converting…"),
        "preprocessing": ("Анализ и подготовка аудио…", "Analyzing and preparing audio…"),
        "transcription": ("Распознавание речи…", "Speech recognition…"),
        "diarization": ("Диаризация…", "Speaker diarization…"),
        "export": ("Экспорт…", "Exporting…"),
        "finalizing": ("Завершение…", "Finalizing…"),
    ]

    public static func text(_ stage: String, english: Bool) -> String {
        guard let pair = stages[stage] else { return stage }
        return english ? pair.en : pair.ru
    }
}
