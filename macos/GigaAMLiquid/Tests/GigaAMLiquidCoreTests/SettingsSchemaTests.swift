import Testing
@testable import GigaAMLiquidCore

@Suite struct SettingsSchemaTests {
    @Test func storedChoiceIsKeptOnlyWhenItIsOneOfTheChoices() {
        #expect(SettingsSchema.choice("onnx", in: SettingsSchema.backends) == "onnx")
        #expect(SettingsSchema.choice("gpu", in: SettingsSchema.backends) == "auto")
        #expect(SettingsSchema.choice(nil, in: SettingsSchema.diarizationEngines) == "pyannote")
    }

    @Test func defaultsComeFirst() {
        #expect(SettingsSchema.models.first == "v3_e2e_rnnt")
        #expect(SettingsSchema.subtitleLines.first == "2")
        #expect(SettingsSchema.subtitleCharacters.first == "64")
        #expect(SettingsSchema.onnxProviders.first == "auto")
    }
}
