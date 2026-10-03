import Testing
@testable import GigaAMLiquidCore

@Suite struct SettingsSchemaTests {
    @Test func storedChoiceIsKeptOnlyWhenItIsOneOfTheChoices() {
        #expect(SettingsSchema.choice("onnx", in: SettingsSchema.backends) == "onnx")
        #expect(SettingsSchema.choice("gpu", in: SettingsSchema.backends) == "auto")
        #expect(SettingsSchema.choice(nil, in: SettingsSchema.diarizationEngines) == "pyannote")
    }

    /// No diarization backend implements live estimates; the worker reports the
    /// mode unavailable at once, so the session simply had no speakers.
    @Test func liveEstimateIsNotOffered() {
        #expect(SettingsSchema.liveDiarizationModes.map(\.value) == ["off", "after_stop"])
        #expect(SettingsSchema.liveDiarizationModes.map(\.title) == ["Выкл.", "После остановки"])
    }

    /// The popup stores its title; a choice saved before estimates were hidden
    /// (or a raw value) reads back as off.
    @Test func storedLiveEstimateFallsBackToOff() {
        #expect(SettingsSchema.liveDiarizationMode(stored: "После остановки") == "after_stop")
        #expect(SettingsSchema.liveDiarizationMode(stored: "after_stop") == "after_stop")
        #expect(SettingsSchema.liveDiarizationMode(stored: "Оценка вживую") == "off")
        #expect(SettingsSchema.liveDiarizationMode(stored: "live_estimate") == "off")
        #expect(SettingsSchema.liveDiarizationMode(stored: nil) == "off")
    }

    @Test func defaultsComeFirst() {
        #expect(SettingsSchema.models.first == "v3_e2e_rnnt")
        #expect(SettingsSchema.subtitleLines.first == "2")
        #expect(SettingsSchema.subtitleCharacters.first == "64")
        #expect(SettingsSchema.onnxProviders.first == "auto")
    }
}
