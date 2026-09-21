//! Interface language and the bilingual string table.
//!
//! Every user-facing text lives in [`STRINGS`] as `(key, ru, en)`; the UI looks
//! texts up with [`t`] / [`tf`] so that `/lang` switches the whole interface at
//! once. The language is shared with the desktop app through its
//! `user_settings.json` (`"language": "ru" | "en"`).

#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub(crate) enum Lang {
    #[default]
    Ru,
    En,
}

impl Lang {
    /// Parses `ru` / `en`, ignoring case and surrounding whitespace.
    pub(crate) fn parse(text: &str) -> Option<Lang> {
        match text.trim().to_ascii_lowercase().as_str() {
            "ru" => Some(Lang::Ru),
            "en" => Some(Lang::En),
            _ => None,
        }
    }

    pub(crate) fn code(self) -> &'static str {
        match self {
            Lang::Ru => "ru",
            Lang::En => "en",
        }
    }

    pub(crate) fn toggle(self) -> Lang {
        match self {
            Lang::Ru => Lang::En,
            Lang::En => Lang::Ru,
        }
    }
}

/// Removes `--lang X` / `--lang=X` from the arguments and returns the requested
/// language, so that the headless parser never sees the flag. An unknown value
/// is an error rather than a silent fallback: a typo must not start the UI in
/// the wrong language.
pub(crate) fn strip_lang(args: Vec<String>) -> Result<(Vec<String>, Option<Lang>), String> {
    let mut result = Vec::with_capacity(args.len());
    let mut lang = None;
    let mut expect_value = false;
    let parse = |value: &str| {
        Lang::parse(value).ok_or_else(|| format!("--lang expects ru or en, got `{value}`"))
    };
    for arg in args {
        if expect_value {
            expect_value = false;
            lang = Some(parse(&arg)?);
        } else if arg == "--lang" {
            expect_value = true;
        } else if let Some(value) = arg.strip_prefix("--lang=") {
            lang = Some(parse(value)?);
        } else {
            result.push(arg);
        }
    }
    if expect_value {
        return Err("--lang expects ru or en".into());
    }
    Ok((result, lang))
}

/// `(key, ru, en)`. Keys are dotted, lower-case; `{name}` placeholders are
/// filled by [`tf`].
pub(crate) const STRINGS: &[(&str, &str, &str)] = &[
    ("app.title", "GigaAM Транскрибатор", "GigaAM Transcriber"),
    ("lang.name", "Русский", "English"),
    ("status.ready", "Готово", "Ready"),
    ("status.running", "Распознавание…", "Recognition running…"),
    ("status.idle", "Ожидание", "Idle"),
    ("tab.processing", "Обработка", "Processing"),
    ("tab.llm", "LLM", "LLM"),
    ("tab.settings", "Настройки", "Settings"),
    ("tab.log", "Журнал", "Log"),
    ("queue.count", "Очередь ({n})", "Queue ({n})"),
    ("status.llm_running", "LLM…", "LLM…"),
    (
        "status.worker_down",
        "воркер недоступен",
        "worker unavailable",
    ),
    ("header.help", "справка", "help"),
    ("hint.prefix", "Далее", "Next"),
    (
        "hint.worker_down",
        "Воркер не запущен: выполните gigaam --update",
        "Worker is not running: run gigaam --update",
    ),
    (
        "hint.cancel_batch",
        "Esc — остановить после текущего файла (Esc×2 — сразу)",
        "Esc stops after the current file (Esc×2 kills at once)",
    ),
    (
        "hint.cancel_llm",
        "Esc — отменить LLM",
        "Esc cancels the LLM run",
    ),
    (
        "hint.add_files",
        "Вставьте путь к файлу/папке и Enter",
        "Paste a file or folder path and press Enter",
    ),
    (
        "hint.start",
        "s или [Запустить] — начать обработку",
        "s or [Start] begins processing",
    ),
    (
        "hint.run_llm",
        "L — выжимка/задачи через LLM",
        "L runs a summary or tasks through the LLM",
    ),
    (
        "hint.view_result",
        "r — показать/скрыть ответ; вкладка Журнал — детали",
        "r shows/hides the answer; the Log tab has the details",
    ),
    ("queue.title", "Очередь", "Queue"),
    (
        "queue.empty",
        "Вставьте путь к файлу или папке и нажмите Enter",
        "Paste a file or folder path and press Enter",
    ),
    ("queue.col_no", "№", "#"),
    ("queue.col_file", "Файл", "File"),
    ("queue.col_state", "Состояние", "State"),
    ("state.pending", "ожидает", "pending"),
    ("state.processing", "в обработке", "processing"),
    ("state.done", "готово", "done"),
    ("state.failed", "ошибка", "error"),
    ("state.cancelled", "отменён", "cancelled"),
    ("params.title", "Параметры", "Parameters"),
    ("params.backend", "Движок", "Engine"),
    ("params.model", "Модель", "Model"),
    ("params.formats", "Форматы", "Formats"),
    ("params.diarize", "Диаризация", "Diarization"),
    ("params.speakers", "Спикеры", "Speakers"),
    ("params.audio", "Звук", "Audio"),
    ("params.output", "Папка", "Folder"),
    ("value.on", "вкл", "on"),
    ("value.off", "выкл", "off"),
    ("value.auto", "авто", "auto"),
    ("value.next_to_file", "рядом с файлом", "next to the file"),
    ("progress.title", "Прогресс", "Progress"),
    ("progress.saved", "Сохранено", "Saved"),
    ("btn.start", "Запустить", "Start"),
    ("btn.stop", "Остановить", "Stop"),
    ("btn.clear", "Очистить", "Clear"),
    ("footer.start", "запустить", "start"),
    ("footer.llm", "LLM", "LLM"),
    ("footer.diar", "диаризация", "diarization"),
    ("footer.formats", "форматы", "formats"),
    ("footer.help", "справка", "help"),
    ("footer.quit", "выход", "quit"),
    ("menu.back", "назад", "back"),
    ("llm.inputs", "Транскрипты", "Transcripts"),
    ("llm.inputs_count", "Транскрипты ({n})", "Transcripts ({n})"),
    (
        "llm.inputs_empty",
        "Нет транскриптов: запустите обработку или /llm-file <путь>",
        "No transcripts: run a transcription or /llm-file <path>",
    ),
    ("llm.col_source", "Откуда", "Source"),
    ("llm.src_session", "сессия", "session"),
    ("llm.src_file", "файл", "file"),
    ("llm.tasks", "Что сделать", "What to do"),
    ("llm.mode_summary", "Выжимка", "Summary"),
    ("llm.mode_tasks", "Задачи", "Tasks"),
    ("llm.mode_terms", "Термины", "Terms"),
    ("llm.mode_custom", "Свой промпт", "Custom prompt"),
    ("llm.prompt", "Промпт", "Prompt"),
    (
        "llm.prompt_empty",
        "— (клик, чтобы задать)",
        "— (click to set)",
    ),
    ("llm.provider", "Провайдер", "Provider"),
    ("llm.not_installed", "не установлен", "not installed"),
    ("llm.broken", "не работает", "broken"),
    ("btn.run_llm", "Запустить LLM", "Run LLM"),
    ("btn.cancel_llm", "Отменить", "Cancel"),
    ("llm.answer", "Ответ", "Answer"),
    ("llm.streaming", "стрим…", "streaming…"),
    ("llm.saved", "сохранено: {name}", "saved: {name}"),
    (
        "llm.answer_empty",
        "Ответ появится здесь: L или [Запустить LLM]",
        "The answer appears here: L or [Run LLM]",
    ),
    (
        "llm.cannot_remove_session",
        "Результаты сессии убирает /clear; Delete — только для /llm-file",
        "Session results leave with /clear; Delete only removes /llm-file entries",
    ),
    ("llm.removed", "Убрано: {name}", "Removed: {name}"),
    ("settings.title", "Настройки", "Settings"),
    (
        "settings.hint",
        "Enter/клик — изменить · ↑↓ — выбор",
        "Enter/click changes · ↑↓ selects",
    ),
    (
        "settings.opened",
        "Вкладка «Настройки»: Enter или клик по строке меняет значение",
        "Settings tab: Enter or a click on a row changes the value",
    ),
    ("settings.language", "Язык", "Language"),
    ("settings.mouse", "Мышь", "Mouse"),
    ("settings.pets", "Пет", "Pet"),
    ("settings.backend", "Движок", "Engine"),
    ("settings.onnx_provider", "ONNX-провайдер", "ONNX provider"),
    ("settings.model", "Модель", "Model"),
    (
        "settings.diarization_backend",
        "Движок диаризации",
        "Diarization engine",
    ),
    ("settings.audio", "Звук", "Audio"),
    ("settings.output", "Папка результатов", "Results folder"),
    (
        "settings.subtitle_split",
        "Субтитры: разбиение по предложениям",
        "Subtitles: sentence splitting",
    ),
    (
        "settings.subtitle_lines",
        "Субтитры: строк в реплике",
        "Subtitles: lines per cue",
    ),
    (
        "settings.subtitle_width",
        "Субтитры: ширина строки",
        "Subtitles: line width",
    ),
    ("settings.llm_provider", "LLM: провайдер", "LLM: provider"),
    ("settings.llm_model", "LLM: модель", "LLM: model"),
    ("settings.llm_api_url", "LLM: API URL", "LLM: API URL"),
    ("settings.llm_api_key", "LLM: ключ API", "LLM: API key"),
    ("settings.llm_temperature", "LLM: температура", "LLM: temperature"),
    ("settings.llm_path", "LLM: путь к CLI", "LLM: CLI path"),
    (
        "settings.llm_provider_name",
        "LLM: внутренний provider",
        "LLM: internal provider",
    ),
    ("settings.llm_args", "LLM: доп. аргументы", "LLM: extra arguments"),
    (
        "settings.llm_tools",
        "LLM: инструменты агента",
        "LLM: agent tools",
    ),
    ("value.default", "по умолчанию", "default"),
    ("value.not_set", "не задан", "not set"),
    ("value.none", "нет", "none"),
    ("log.title", "Журнал", "Log"),
    ("log.empty", "Журнал пуст", "The log is empty"),
    (
        "log.hint",
        "End — в конец · Ctrl+L — очистить",
        "End jumps to the end · Ctrl+L clears",
    ),
    ("help.title", "Справка", "Help"),
    (
        "help.close",
        "Esc, ? или клик — закрыть",
        "Esc, ? or a click closes",
    ),
    ("help.keys", "Клавиши", "Keys"),
    ("help.commands", "Команды", "Commands"),
    (
        "help.key_tab",
        "следующая/предыдущая вкладка",
        "next / previous tab",
    ),
    ("help.key_fn", "вкладки напрямую", "jump to a tab"),
    (
        "help.key_s",
        "запустить обработку очереди",
        "start processing the queue",
    ),
    (
        "help.key_l",
        "запустить LLM по результатам",
        "run the LLM on the results",
    ),
    ("help.key_r", "показать ответ LLM", "show the LLM answer"),
    ("help.key_d", "диаризация вкл/выкл", "diarization on/off"),
    ("help.key_f", "форматы txt / txt+srt", "formats txt / txt+srt"),
    ("help.key_help", "эта справка", "this help"),
    (
        "help.key_esc",
        "закрыть меню/панель; остановить (×2 — сразу); выход (×2)",
        "close a menu/panel; stop (×2 kills); quit (×2)",
    ),
    (
        "help.key_q",
        "выход при пустой строке ввода",
        "quit when the input is empty",
    ),
    ("help.key_ctrl_c", "выход (нажать дважды)", "quit (press twice)"),
    (
        "help.key_arrows",
        "очередь, параметры, транскрипты и режимы, настройки, журнал",
        "queue, parameters, transcripts and modes, settings, log",
    ),
    (
        "help.key_left_right",
        "Обработка: очередь ↔ параметры",
        "Processing: queue ↔ parameters",
    ),
    (
        "help.key_enter",
        "выполнить команду; открыть меню строки",
        "run a command; open the row's menu",
    ),
    (
        "help.key_space",
        "LLM: переключить режим под курсором",
        "LLM: toggle the mode under the cursor",
    ),
    (
        "help.key_delete",
        "убрать файл из очереди / транскрипт LLM",
        "remove the queued file / LLM transcript",
    ),
    (
        "help.key_ctrl_arrows",
        "переставить файл в очереди",
        "reorder the queue",
    ),
    (
        "help.key_page",
        "прокрутка ответа, журнала, справки",
        "scroll the answer, the log, the help",
    ),
    ("help.key_ctrl_l", "очистить журнал", "clear the log"),
    (
        "help.mouse_note",
        "Мышь: клики и колёсико работают везде. Для выделения текста зажмите Shift (Linux/Windows) или Option (macOS) либо выключите мышь в настройках.",
        "Mouse: clicks and the wheel work everywhere. To select text hold Shift (Linux/Windows) or Option (macOS), or turn the mouse off in the settings.",
    ),
    (
        "settings.language_changed",
        "Язык: Русский",
        "Language: English",
    ),
    ("usage.lang", "Usage: /lang ru|en", "Usage: /lang ru|en"),
    ("settings.mouse_on", "Мышь: вкл", "Mouse: on"),
    (
        "settings.mouse_off",
        "Мышь: выкл (Shift/Option + мышь — выделение текста)",
        "Mouse: off (Shift/Option + mouse selects text)",
    ),
    (
        "usage.mouse",
        "Usage: /mouse on|off",
        "Usage: /mouse on|off",
    ),
    // Stage labels: the same texts as `_STAGE_NAMES` in src/gui/processing_mixin.py.
    ("stage.preparing", "Подготовка…", "Preparing…"),
    ("stage.conversion", "Конвертация…", "Converting…"),
    (
        "stage.preprocessing",
        "Анализ и подготовка аудио…",
        "Analyzing and preparing audio…",
    ),
    (
        "stage.transcription",
        "Распознавание речи…",
        "Speech recognition…",
    ),
    ("stage.diarization", "Диаризация…", "Speaker diarization…"),
    ("stage.export", "Экспорт…", "Exporting…"),
    ("stage.finalizing", "Завершение…", "Finalizing…"),
    // Command descriptions: the English texts mirror `COMMANDS` in commands.rs,
    // which headless mode and the README keep in English.
    (
        "cmd.output",
        "папка для результатов",
        "set the results directory",
    ),
    (
        "cmd.backend",
        "выбрать ASR-рантайм",
        "select the ASR runtime",
    ),
    (
        "cmd.onnx-provider",
        "выбрать провайдер исполнения ONNX",
        "select the ONNX execution provider",
    ),
    (
        "cmd.model",
        "выбрать модель распознавания GigaAM",
        "select the GigaAM recognition model",
    ),
    (
        "cmd.formats",
        "форматы вывода, например txt,srt",
        "output formats, e.g. txt,srt",
    ),
    (
        "cmd.subtitle-split",
        "включить или выключить разбиение по предложениям",
        "turn sentence splitting on or off",
    ),
    (
        "cmd.subtitle-lines",
        "1–4 строки в реплике субтитров",
        "set 1-4 lines per subtitle cue",
    ),
    (
        "cmd.subtitle-width",
        "20–100 символов в строке субтитров",
        "set 20-100 characters per subtitle line",
    ),
    (
        "cmd.diarize",
        "включить или выключить диаризацию говорящих",
        "turn speaker diarization on or off",
    ),
    (
        "cmd.audio-mode",
        "предобработка аудио: auto, off, light, denoise",
        "audio preprocessing: auto, off, light, denoise",
    ),
    (
        "cmd.diarization-backend",
        "выбрать ONNX, pyannote или NVIDIA Sortformer",
        "select ONNX, pyannote, or NVIDIA Sortformer",
    ),
    (
        "cmd.speakers",
        "auto или фиксированное число говорящих",
        "auto or a fixed speaker count",
    ),
    (
        "cmd.remove",
        "убрать файл из очереди по номеру",
        "remove a file from the queue by number",
    ),
    (
        "cmd.clear",
        "очистить очередь и список результатов",
        "clear the queue and result list",
    ),
    (
        "cmd.llm-file",
        "добавить файл транскрипта (.txt/.md/.srt/.vtt) для LLM",
        "add a transcript file (.txt/.md/.srt/.vtt) for the LLM",
    ),
    (
        "cmd.settings",
        "открыть вкладку «Настройки»",
        "open the Settings tab",
    ),
    ("cmd.help", "показать клавиши и команды", "show the keys and commands"),
    (
        "cmd.pets",
        "показать или скрыть анимированного единорога",
        "toggle the animated unicorn companion",
    ),
    (
        "cmd.llm-mode",
        "summary, tasks, terms или custom",
        "summary, tasks, terms, or custom",
    ),
    (
        "cmd.llm-prompt",
        "задать свой промпт для LLM",
        "set a custom LLM prompt",
    ),
    (
        "cmd.llm-run",
        "запустить обработку LLM",
        "run LLM processing",
    ),
    ("cmd.llm-api-url", "задать URL LLM API", "set LLM API URL"),
    ("cmd.llm-api-key", "задать ключ LLM API", "set LLM API key"),
    ("cmd.llm-model", "задать модель LLM", "set LLM model"),
    (
        "cmd.llm-temperature",
        "задать температуру LLM",
        "set LLM temperature",
    ),
    (
        "cmd.llm-provider-name",
        "внутренний провайдер Pi/oh-my-pi, например anthropic",
        "Pi/oh-my-pi internal provider, e.g. anthropic",
    ),
    (
        "cmd.llm-args",
        "дополнительные аргументы CLI для текущего провайдера LLM",
        "extra CLI arguments for the current LLM provider",
    ),
    (
        "cmd.llm-path",
        "путь к CLI-бинарнику текущего провайдера",
        "path to the current provider's CLI binary",
    ),
    (
        "cmd.llm-tools",
        "on|off · разрешить CLI-агенту инструменты и сессии",
        "on|off · let the CLI agent use tools and sessions",
    ),
    (
        "cmd.lang",
        "язык интерфейса: ru или en",
        "interface language: ru or en",
    ),
    ("cmd.mouse", "мышь: on или off", "mouse support: on or off"),
    (
        "cmd.exit",
        "выйти из терминального интерфейса",
        "exit the terminal UI",
    ),
];

/// Looks a key up in [`STRINGS`] and returns `None` when it is absent, for keys
/// built at runtime (worker stage ids) that may name a stage the table does not know.
pub(crate) fn try_t(lang: Lang, key: &str) -> Option<&'static str> {
    STRINGS
        .iter()
        .find(|(k, _, _)| *k == key)
        .map(|(_, ru, en)| match lang {
            Lang::Ru => *ru,
            Lang::En => *en,
        })
}

/// Looks a key up in [`STRINGS`]. A missing key is a programming error caught
/// by `keys_used_in_sources_exist`; at runtime it renders as `??` rather than
/// panicking in the draw loop.
pub(crate) fn t(lang: Lang, key: &str) -> &'static str {
    match STRINGS.iter().find(|(k, _, _)| *k == key) {
        Some((_, ru, en)) => match lang {
            Lang::Ru => ru,
            Lang::En => en,
        },
        None => {
            debug_assert!(false, "missing i18n key {key}");
            "??"
        }
    }
}

/// [`t`] with `{name}` placeholders replaced from `args`.
pub(crate) fn tf(lang: Lang, key: &str, args: &[(&str, &str)]) -> String {
    let mut text = t(lang, key).to_owned();
    for (name, value) in args {
        text = text.replace(&format!("{{{name}}}"), value);
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_string_has_both_languages_and_unique_keys() {
        let mut seen = std::collections::HashSet::new();
        for (key, ru, en) in STRINGS {
            assert!(!ru.is_empty() && !en.is_empty(), "{key}");
            assert!(seen.insert(*key), "duplicate key {key}");
        }
    }

    #[test]
    fn every_command_has_a_description_key() {
        for (name, _) in crate::commands::COMMANDS {
            let key = format!("cmd.{}", name.trim_start_matches('/'));
            assert!(STRINGS.iter().any(|(k, _, _)| *k == key), "{key}");
        }
    }

    #[test]
    fn keys_used_in_sources_exist() {
        let re = regex_lite::Regex::new(r#"\bt[f]?\(\s*[a-z_.]+\s*,\s*"([a-z0-9_.-]+)""#).unwrap();
        for file in [
            "app.rs",
            "commands.rs",
            "main.rs",
            "ui/mod.rs",
            "ui/processing.rs",
            "ui/llm.rs",
            "ui/settings.rs",
            "ui/log.rs",
            "ui/help.rs",
            "ui/menu.rs",
            "keys.rs",
        ] {
            let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("src")
                .join(file);
            let Ok(src) = std::fs::read_to_string(&path) else {
                continue;
            };
            for cap in re.captures_iter(&src) {
                let key = &cap[1];
                assert!(
                    STRINGS.iter().any(|(k, _, _)| *k == key),
                    "{file}: missing key {key}"
                );
            }
        }
    }

    #[test]
    fn tf_substitutes_named_arguments() {
        assert_eq!(tf(Lang::Ru, "queue.count", &[("n", "3")]), "Очередь (3)");
        assert_eq!(tf(Lang::En, "queue.count", &[("n", "3")]), "Queue (3)");
    }

    #[test]
    fn lang_parses_codes_and_toggles() {
        assert_eq!(Lang::parse("EN "), Some(Lang::En));
        assert_eq!(Lang::parse("ru"), Some(Lang::Ru));
        assert_eq!(Lang::parse("xx"), None);
        assert_eq!(Lang::Ru.toggle(), Lang::En);
        assert_eq!(Lang::En.code(), "en");
        assert_eq!(t(Lang::Ru, "lang.name"), "Русский");
    }

    #[test]
    fn strip_lang_removes_both_forms_and_rejects_unknown_values() {
        let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(
            strip_lang(args(&["--lang", "en", "transcribe", "a.wav"])).unwrap(),
            (args(&["transcribe", "a.wav"]), Some(Lang::En))
        );
        assert_eq!(
            strip_lang(args(&["transcribe", "--lang=ru"])).unwrap(),
            (args(&["transcribe"]), Some(Lang::Ru))
        );
        assert_eq!(
            strip_lang(args(&["transcribe"])).unwrap(),
            (args(&["transcribe"]), None)
        );
        assert!(strip_lang(args(&["--lang", "xx"])).is_err());
        assert!(strip_lang(args(&["--lang"])).is_err());
    }
}
