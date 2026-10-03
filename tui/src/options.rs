//! Every enumerable option the TUI offers, in one place: the command menus, the
//! command validators, the settings loader and headless mode all read these
//! tables. They used to be re-listed per call site (the ONNX providers three
//! times, the diarization backends and audio modes four times), so adding a
//! value meant finding every copy.

/// Output formats, in menu order; the keys of the desktop app's `output_formats`.
pub(crate) const FORMAT_KEYS: [&str; 7] = [
    "txt",
    "txt_timecodes",
    "txt_diarize",
    "txt_diarize_timecodes",
    "md",
    "srt",
    "vtt",
];

/// GigaAM models: the id the worker takes and the label the menu shows.
pub(crate) const MODEL_OPTIONS: [(&str, &str); 3] = [
    ("v3_e2e_rnnt", "GigaAM v3 e2e RNNT (current)"),
    ("multilingual_ctc", "Multilingual CTC (220M)"),
    ("multilingual_large_ctc", "Multilingual Large CTC (600M)"),
];

pub(crate) const ONNX_PROVIDERS: [&str; 6] =
    ["auto", "cpu", "cuda", "tensorrt", "coreml", "directml"];

pub(crate) const AUDIO_MODES: [&str; 4] = ["auto", "off", "light", "denoise"];

pub(crate) const DIARIZATION_BACKENDS: [&str; 3] = ["pyannote", "onnx", "sortformer"];

/// The `/speakers` menu: automatic or a fixed count.
pub(crate) const SPEAKER_CHOICES: [&str; 9] = ["auto", "1", "2", "3", "4", "5", "6", "7", "8"];

/// LLM modes, top to bottom: the worker's mode id and its label key.
pub(crate) const LLM_MODES: [(&str, &str); 4] = [
    ("summary", "llm.mode_summary"),
    ("tasks", "llm.mode_tasks"),
    ("terms", "llm.mode_terms"),
    ("custom", "llm.mode_custom"),
];

/// The rows of the Processing page's parameter panel, top to bottom: the label key
/// and the command whose menu (or pre-filled command line) the row opens.
pub(crate) const PARAM_ROWS: [(&str, &str); 7] = [
    ("params.backend", "/backend"),
    ("params.model", "/model"),
    ("params.formats", "/formats"),
    ("params.diarize", "/diarize"),
    ("params.speakers", "/speakers"),
    ("params.audio", "/audio-mode"),
    ("params.output", "/output"),
];

/// ASR runtimes this build can run; MLX exists on macOS only.
pub(crate) fn selectable_backends() -> &'static [&'static str] {
    #[cfg(target_os = "macos")]
    {
        &["auto", "pytorch", "mlx", "onnx"]
    }
    #[cfg(not(target_os = "macos"))]
    {
        &["auto", "pytorch", "onnx"]
    }
}

pub(crate) fn backend_is_supported(backend: &str) -> bool {
    selectable_backends().contains(&backend)
}

pub(crate) fn is_model(id: &str) -> bool {
    MODEL_OPTIONS.iter().any(|(model, _)| *model == id)
}

pub(crate) fn is_llm_mode(id: &str) -> bool {
    LLM_MODES.iter().any(|(mode, _)| *mode == id)
}

/// `a|b|c` for usage and error messages.
pub(crate) fn alternatives(values: &[&str]) -> String {
    values.join("|")
}

pub(crate) fn model_ids() -> Vec<&'static str> {
    MODEL_OPTIONS.iter().map(|(id, _)| *id).collect()
}

pub(crate) fn llm_mode_ids() -> Vec<&'static str> {
    LLM_MODES.iter().map(|(id, _)| *id).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validators_accept_exactly_the_listed_values() {
        for backend in selectable_backends() {
            assert!(backend_is_supported(backend));
        }
        assert!(!backend_is_supported("tpu"));
        assert!(is_model("multilingual_ctc") && !is_model("v2"));
        assert!(is_llm_mode("terms") && !is_llm_mode("poem"));
        assert_eq!(alternatives(&AUDIO_MODES), "auto|off|light|denoise");
        assert_eq!(llm_mode_ids(), ["summary", "tasks", "terms", "custom"]);
    }
}
