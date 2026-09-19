import Testing
@testable import GigaAMLiquidCore

/// The table itself is generated from src/core/log_i18n.py (checked by pytest);
/// these tests cover the Swift matcher: groups, recursion, pass-through.
@Suite struct LogTranslationTests {
    @Test func dynamicLinesKeepTheirValues() {
        #expect(LogTranslation.english("Файл 2 из 5: interview.mp3") == "File 2 of 5: interview.mp3")
        #expect(LogTranslation.english("Сохранён файл: interview.txt") == "Saved file: interview.txt")
        #expect(LogTranslation.english("Готово за 2 мин 3 сек (подготовка звука 1.2 с, распознавание 118.4 с)")
                == "Done in 2 min 3 s (audio preparation 1.2 s, recognition 118.4 s)")
        #expect(LogTranslation.english("Не удалось обработать a.mp3: boom") == "Could not process a.mp3: boom")
    }

    @Test func capturedGroupsAreTranslatedRecursively() {
        #expect(LogTranslation.english("  Почему: в записи постоянный фоновый шум") == "  Why: constant background noise in the recording")
        #expect(LogTranslation.english("Очистка звука: лёгкая очистка от шума (режим auto)") == "Audio cleanup: light noise cleanup (mode auto)")
        #expect(LogTranslation.english("  Очистка не применена: результат очистки отклонён: очистка заглушила часть речи")
                == "  Cleanup not applied: cleanup result rejected: cleanup silenced part of the speech")
    }

    @Test func unknownLinesAndMultilineTextPassThrough() {
        #expect(LogTranslation.english("Что-то новое") == "Что-то новое")
        #expect(LogTranslation.englishText("Модель готова.\n/tmp/x.wav\nРаспознаём речь…") == "Model ready.\n/tmp/x.wav\nRecognizing speech…")
    }
}
