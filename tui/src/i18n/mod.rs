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

pub(crate) use strings::STRINGS;

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

/// `n` with the noun from a `plural.*` key in the right form: «1 файл, 2 файла,
/// 5 файлов» / «1 file, 2 files». The ru text lists the forms for 1, 2–4 and 5+
/// (`|`-separated), the en text the singular and the plural.
pub(crate) fn tn(lang: Lang, n: usize, key: &str) -> String {
    let forms: Vec<&str> = t(lang, key).split('|').collect();
    let index = match lang {
        Lang::En => usize::from(n != 1),
        Lang::Ru => match (n % 10, n % 100) {
            (1, tens) if !(11..=14).contains(&tens) => 0,
            (2..=4, tens) if !(11..=14).contains(&tens) => 1,
            _ => 2,
        },
    };
    let form = forms
        .get(index)
        .or_else(|| forms.last())
        .copied()
        .unwrap_or_default();
    format!("{n} {form}")
}

mod strings;

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

    /// Every `.rs` file under `src/`, as `/`-separated paths relative to it. The
    /// source scanners below walk the tree instead of listing files: a hand-kept
    /// list silently stops covering a module once it is added or moved.
    fn rust_sources() -> Vec<(String, String)> {
        fn walk(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
            for entry in std::fs::read_dir(dir).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    walk(&path, out);
                } else if path.extension().is_some_and(|ext| ext == "rs") {
                    out.push(path);
                }
            }
        }
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut files = Vec::new();
        walk(&root, &mut files);
        files.sort();
        let sources: Vec<(String, String)> = files
            .into_iter()
            .map(|path| {
                let relative = path
                    .strip_prefix(&root)
                    .unwrap()
                    .components()
                    .map(|part| part.as_os_str().to_string_lossy().into_owned())
                    .collect::<Vec<_>>()
                    .join("/");
                (relative, std::fs::read_to_string(&path).unwrap())
            })
            .collect();
        assert!(sources.iter().any(|(file, _)| file == "ui/processing.rs"));
        sources
    }

    #[test]
    fn keys_used_in_sources_exist() {
        // `t(lang, "key")`, `tf(lang, "key", …)` and `tn(lang, n, "key")`.
        let re = regex_lite::Regex::new(
            r#"\bt[fn]?\(\s*[a-z_.]+\s*,\s*(?:[a-z_.()]+\s*,\s*)?"([a-z0-9_.-]+)""#,
        )
        .unwrap();
        for (file, src) in rust_sources() {
            if file == "i18n/mod.rs" {
                continue; // its doc comments spell the call shapes with placeholder keys
            }
            for cap in re.captures_iter(&src) {
                let key = &cap[1];
                assert!(
                    STRINGS.iter().any(|(k, _, _)| *k == key),
                    "{file}: missing key {key}"
                );
            }
        }
    }

    /// English texts that may still be assigned to the status line or the log as
    /// literals. Empty: every message goes through [`t`] / [`tf`].
    const ALLOWED_LITERAL_STATUSES: &[&str] = &[];

    /// Sources the English-literal scan skips, each for a stated reason; every
    /// other file under `src/` is scanned, including modules added later.
    const NOT_INTERACTIVE_SOURCES: &[&str] = &[
        // Agent-facing output stays English.
        "cli/args.rs",
        "cli/headless.rs",
        // Its file errors carry OS text and reach the status line only through
        // `err.settings_save`.
        "settings/store.rs",
        // Provider names and the JSON protocol.
        "worker.rs",
        // The string table itself.
        "i18n/strings.rs",
    ];

    /// Scans the interactive sources for English literals reaching `status`, the
    /// log, or a `String` through `.into()` / `unwrap_or`, outside their test
    /// modules. [`NOT_INTERACTIVE_SOURCES`] lists the files left out and why.
    #[test]
    fn no_english_literal_reaches_the_status_line_or_the_log() {
        let patterns = [
            r#"(?:app|self)\.status = "([A-Z][^"]*)""#,
            r#"(?:app|self)\.log\("([A-Z][^"]*)""#,
            r#"(?:app|self)\.status = format!\("((?:[A-Z][a-z]+|[A-Z]{2,})[^"{]*)"#,
            r#"(?:app|self)\.log\(format!\("((?:[A-Z][a-z]+|[A-Z]{2,})[^"{]*)"#,
            r#""([A-Z][a-z]+(?: [^"]*)?)"\.into\(\)"#,
            r#""([A-Z][a-z]+(?: [^"]*)?)"\.to_owned\(\)"#,
            r#"unwrap_or(?:_else)?\((?:\|\| )?"([A-Z][^"]*)""#,
            r#"Err\(\s*"([A-Z][^"]*)""#,
            r#"format!\(\s*"((?:[A-Z][a-z]+|[A-Z]{2,}) [^"{]*)"#,
            // A message continued on its own line inside a multi-line `format!(`.
            r#"^\s*"((?:[A-Z][a-z]+|[A-Z]{2,}) [^"]*)","#,
        ]
        .map(|pattern| regex_lite::Regex::new(pattern).unwrap());
        let mut found = Vec::new();
        for (file, src) in rust_sources() {
            // A `tests.rs` module and the test fixtures are test code as a whole.
            let test_only =
                file == "test_support.rs" || file.rsplit('/').next() == Some("tests.rs");
            if test_only || NOT_INTERACTIVE_SOURCES.contains(&file.as_str()) {
                continue;
            }
            let src = src.split("#[cfg(test)]").next().unwrap_or_default();
            for (offset, line) in src.lines().enumerate() {
                if line.trim_start().starts_with("//") {
                    continue;
                }
                for re in &patterns {
                    for cap in re.captures_iter(line) {
                        let literal = cap[1].to_owned();
                        if !ALLOWED_LITERAL_STATUSES.contains(&literal.as_str()) {
                            found.push(format!("{file}:{}: {literal}", offset + 1));
                        }
                    }
                }
            }
        }
        found.sort();
        found.dedup();
        assert!(
            found.is_empty(),
            "English literals must go through t()/tf():\n{}",
            found.join("\n")
        );
    }

    #[test]
    fn tf_substitutes_named_arguments() {
        assert_eq!(tf(Lang::Ru, "queue.count", &[("n", "3")]), "Очередь (3)");
        assert_eq!(tf(Lang::En, "queue.count", &[("n", "3")]), "Queue (3)");
    }

    #[test]
    fn tn_picks_the_russian_and_english_plural_forms() {
        let ru = |n| tn(Lang::Ru, n, "plural.files");
        assert_eq!(ru(1), "1 файл");
        assert_eq!(ru(2), "2 файла");
        assert_eq!(ru(5), "5 файлов");
        assert_eq!(ru(11), "11 файлов");
        assert_eq!(ru(21), "21 файл");
        assert_eq!(ru(24), "24 файла");
        assert_eq!(ru(112), "112 файлов");
        assert_eq!(tn(Lang::En, 1, "plural.files"), "1 file");
        assert_eq!(tn(Lang::En, 0, "plural.files"), "0 files");
        assert_eq!(tn(Lang::En, 3, "plural.results"), "3 results");
        for (key, ru, en) in STRINGS.iter().filter(|(k, _, _)| k.starts_with("plural.")) {
            assert_eq!(ru.split('|').count(), 3, "{key}");
            assert_eq!(en.split('|').count(), 2, "{key}");
        }
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
