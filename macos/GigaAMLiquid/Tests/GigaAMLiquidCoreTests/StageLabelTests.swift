import Testing
@testable import GigaAMLiquidCore

@Suite struct StageLabelTests {
    @Test func everyWorkerStageHasARussianAndEnglishLabel() {
        // Те же семь стадий, что в src/core/progress.py:ProgressStage.
        let stages = ["preparing", "conversion", "preprocessing", "transcription", "diarization", "export", "finalizing"]
        for stage in stages {
            #expect(StageLabel.text(stage, english: false) != stage, "no Russian label for \(stage)")
            #expect(StageLabel.text(stage, english: true) != stage, "no English label for \(stage)")
        }
        #expect(StageLabel.text("preprocessing", english: false) == "Анализ и подготовка аудио…")
        #expect(StageLabel.text("transcription", english: true) == "Speech recognition…")
    }

    @Test func unknownStageIsShownAsIs() {
        #expect(StageLabel.text("warmup", english: false) == "warmup")
    }
}
