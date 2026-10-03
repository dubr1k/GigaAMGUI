import Foundation

/// Choices of the processing settings, shared by every page that shows them
/// (Обработка, Live, Настройки) and by the code that reads them back. The first
/// value is the default. The Python worker validates the same sets
/// (src/config.py, src/tui_worker.py); pytest compares them.
public enum SettingsSchema {
    public static let backends = ["auto", "mlx", "onnx", "pytorch"]
    public static let models = ["v3_e2e_rnnt", "multilingual_ctc", "multilingual_large_ctc"]
    public static let onnxProviders = ["auto", "cpu", "cuda", "tensorrt", "coreml", "directml"]
    public static let diarizationEngines = ["pyannote", "onnx", "sortformer"]
    public static let audioPreprocessing = ["auto", "off", "light", "denoise"]
    /// Subtitle block lines and characters per line, as popup titles.
    public static let subtitleLines = ["2", "1", "3", "4"]
    public static let subtitleCharacters = ["64", "42", "80"]

    /// `value` when it is one of `choices`, otherwise the default (the first choice).
    public static func choice(_ value: String?, in choices: [String]) -> String {
        guard let value, choices.contains(value) else { return choices[0] }
        return value
    }
}
