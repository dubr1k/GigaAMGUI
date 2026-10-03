//! The command table, suggestions and completion.

use crate::{
    i18n::{tf, Lang},
    options::{alternatives, selectable_backends},
    theme::Theme,
};

/// Menu entries that are not values: the UI shows them through `menu.back` /
/// `menu.enter_manually`, the code compares against these identifiers.
pub(crate) const BACK_MENU_OPTION: &str = "← Back";
pub(crate) const ENTER_MANUALLY_OPTION: &str = "Enter manually";

pub(crate) const COMMANDS: [(&str, &str); 39] = [
    (
        "/reconnect",
        "reconnect the worker without repeating processing",
    ),
    (
        "/output",
        "set the results directory; - for next to the file",
    ),
    ("/backend", "select the ASR runtime"),
    ("/onnx-provider", "select the ONNX execution provider"),
    ("/model", "select the GigaAM recognition model"),
    ("/formats", "output formats, e.g. txt,srt"),
    ("/subtitle-split", "turn sentence splitting on or off"),
    ("/subtitle-lines", "set 1-4 lines per subtitle cue"),
    ("/subtitle-width", "set 20-100 characters per subtitle line"),
    ("/diarize", "turn speaker diarization on or off"),
    (
        "/audio-mode",
        "audio preprocessing: auto, off, light, denoise",
    ),
    (
        "/diarization-backend",
        "select ONNX, pyannote, or NVIDIA Sortformer",
    ),
    ("/speakers", "auto or a fixed speaker count"),
    ("/remove", "remove a file from the queue by number"),
    ("/clear", "clear the queue; keep saved results"),
    (
        "/llm-file",
        "add a transcript file (.txt/.md/.srt/.vtt) for the LLM",
    ),
    ("/settings", "open the Settings tab"),
    ("/help", "show the keys and commands"),
    ("/pets", "toggle the animated unicorn companion"),
    ("/llm-mode", "summary, tasks, terms, or custom"),
    ("/llm-prompt", "set a custom LLM prompt"),
    ("/llm-run", "run LLM processing"),
    ("/llm-api-url", "set LLM API URL"),
    ("/llm-api-key", "set LLM API key"),
    ("/llm-model", "set LLM model"),
    ("/llm-temperature", "set LLM temperature"),
    (
        "/llm-provider-name",
        "Pi/oh-my-pi internal provider, e.g. anthropic",
    ),
    (
        "/llm-args",
        "extra CLI arguments for the current LLM provider",
    ),
    ("/llm-path", "path to the current provider's CLI binary"),
    (
        "/llm-tools",
        "on|off · let the CLI agent use tools and sessions",
    ),
    ("/lang", "interface language: ru or en"),
    ("/mouse", "mouse support: on or off"),
    ("/theme", "colour scheme: a name, or a menu without one"),
    ("/add", "enter file or folder paths"),
    ("/undo", "restore the removed queue item"),
    ("/retry", "retry failed files"),
    ("/run-selected", "process the selected file"),
    ("/retry-input", "retry the last failed input"),
    ("/exit", "exit the terminal UI"),
];

pub(crate) fn backend_usage(lang: Lang) -> String {
    tf(
        lang,
        "usage.backend",
        &[("backends", &alternatives(selectable_backends()))],
    )
}

pub(crate) fn command_suggestions(input: &str) -> Vec<(&'static str, &'static str)> {
    let command = input.split_whitespace().next().unwrap_or_default();
    if !command.starts_with('/') {
        return Vec::new();
    }
    COMMANDS
        .into_iter()
        .filter(|(name, _)| name.starts_with(command))
        .collect()
}

/// Tab completion of `/theme <prefix>`: the longest common prefix of the matching
/// theme names (the full name when only one matches).
pub(crate) fn complete_theme_name(input: &str) -> Option<String> {
    let prefix = input.strip_prefix("/theme ")?.trim_start();
    let matches: Vec<&str> = Theme::names()
        .into_iter()
        .filter(|name| name.starts_with(prefix))
        .collect();
    let first = matches.first()?;
    // Byte offsets are char boundaries here: theme names are ASCII (asserted by a
    // theme.rs test).
    let common = matches.iter().fold(first.len(), |common, name| {
        first
            .bytes()
            .zip(name.bytes())
            .take(common)
            .take_while(|(a, b)| a == b)
            .count()
    });
    Some(format!("/theme {}", &first[..common]))
}

pub(crate) fn is_command(input: &str) -> bool {
    !command_suggestions(input).is_empty()
}
