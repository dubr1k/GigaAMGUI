//! Редактирование строки и разбор путей, независимо от горячих клавиш.

#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub(crate) enum InputMode {
    #[default]
    Hidden,
    Paths,
    Command,
    Argument(&'static str),
}

#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub(crate) struct InputState {
    pub mode: InputMode,
    text: String,
    cursor: usize,
}

impl std::ops::Deref for InputState {
    type Target = str;
    fn deref(&self) -> &str {
        self.text()
    }
}

impl InputState {
    pub(crate) fn text(&self) -> &str {
        &self.text
    }
    pub(crate) fn cursor(&self) -> usize {
        self.cursor
    }
    pub(crate) fn open(&mut self, mode: InputMode, text: String) {
        self.mode = mode;
        self.replace(text);
    }
    pub(crate) fn replace(&mut self, text: String) {
        self.cursor = text.len();
        self.text = text;
        if self.mode == InputMode::Hidden && !self.text.is_empty() {
            self.mode = if crate::commands::is_command(&self.text) {
                InputMode::Command
            } else {
                InputMode::Paths
            };
        }
    }
    pub(crate) fn insert(&mut self, text: &str) {
        self.text.insert_str(self.cursor, text);
        self.cursor += text.len();
    }
    pub(crate) fn left(&mut self) {
        self.cursor = self.text[..self.cursor]
            .char_indices()
            .last()
            .map_or(0, |(i, _)| i);
    }
    pub(crate) fn right(&mut self) {
        if let Some(c) = self.text[self.cursor..].chars().next() {
            self.cursor += c.len_utf8();
        }
    }
    pub(crate) fn home(&mut self) {
        self.cursor = 0;
    }
    pub(crate) fn end(&mut self) {
        self.cursor = self.text.len();
    }
    pub(crate) fn backspace(&mut self) {
        let end = self.cursor;
        self.left();
        self.text.drain(self.cursor..end);
    }
    pub(crate) fn delete(&mut self) {
        if let Some(c) = self.text[self.cursor..].chars().next() {
            self.text.drain(self.cursor..self.cursor + c.len_utf8());
        }
    }
    pub(crate) fn clear(&mut self) {
        self.text.clear();
        self.cursor = 0;
    }
    pub(crate) fn close(&mut self) {
        self.clear();
        self.mode = InputMode::Hidden;
    }
}

/// Whether `\` escapes the next character of a pasted path list. Terminals on
/// macOS/Linux drop paths shell-escaped (`My\ File.wav`); on Windows `\` is the
/// path separator, and treating it as an escape turned `C:\My Files\a.wav` into
/// `C:My Filesa.wav`. The parsers take the rule as a parameter so both are
/// tested on every platform.
pub(crate) const BACKSLASH_ESCAPES: bool = cfg!(not(windows));

enum Token {
    Char(char),
    Escaped(char),
    /// A quote or an escaping backslash: shell syntax, not part of the path.
    Syntax,
    Separator,
}

/// The shell quoting rules shared by splitting and by the completeness check.
struct ShellLexer {
    backslash_escapes: bool,
    quote: Option<char>,
    escaped: bool,
}

impl ShellLexer {
    fn new(backslash_escapes: bool) -> Self {
        Self {
            backslash_escapes,
            quote: None,
            escaped: false,
        }
    }

    fn next(&mut self, ch: char) -> Token {
        if self.escaped {
            self.escaped = false;
            return Token::Escaped(ch);
        }
        // Inside quotes a backslash is literal: a quoted Windows path, or a
        // quoted POSIX name that really contains one.
        if ch == '\\' && self.backslash_escapes && self.quote.is_none() {
            self.escaped = true;
            return Token::Syntax;
        }
        if matches!(ch, '\'' | '"') {
            if self.quote == Some(ch) {
                self.quote = None;
                return Token::Syntax;
            }
            if self.quote.is_none() {
                self.quote = Some(ch);
                return Token::Syntax;
            }
        }
        // Finder uses NBSP before «— копия». Shells do not treat Unicode
        // spaces as separators, so preserve them as part of the filename.
        if self.quote.is_none() && matches!(ch, ' ' | '\t' | '\r' | '\n') {
            return Token::Separator;
        }
        Token::Char(ch)
    }

    /// Unterminated quote or a trailing escape: the input is not finished yet.
    fn open(&self) -> bool {
        self.quote.is_some() || self.escaped
    }
}

pub(crate) fn split_paths(raw: &str, backslash_escapes: bool) -> Vec<String> {
    let mut lexer = ShellLexer::new(backslash_escapes);
    let mut paths = Vec::new();
    let mut current = String::new();
    for character in raw.chars() {
        match lexer.next(character) {
            Token::Char(ch) | Token::Escaped(ch) => current.push(ch),
            Token::Syntax => {}
            Token::Separator => {
                if !current.is_empty() {
                    paths.push(std::mem::take(&mut current));
                }
            }
        }
    }
    if lexer.escaped {
        current.push('\\');
    }
    if !current.is_empty() {
        paths.push(current);
    }
    paths
}

/// Только лёгкая проверка завершённости ввода; окончательная валидация — в worker.
pub(crate) fn local_path(raw: &str) -> Option<std::path::PathBuf> {
    let text = raw.trim();
    let decoded;
    let text = if let Some(uri) = text.strip_prefix("file://") {
        let path = uri.strip_prefix("localhost").unwrap_or(uri);
        if !path.starts_with('/') || path.contains(['?', '#']) {
            return None;
        }
        let mut bytes = Vec::new();
        let mut chars = path.as_bytes().iter().copied();
        while let Some(byte) = chars.next() {
            if byte == b'%' {
                let hi = (chars.next()? as char).to_digit(16)?;
                let lo = (chars.next()? as char).to_digit(16)?;
                bytes.push((hi * 16 + lo) as u8);
            } else {
                bytes.push(byte);
            }
        }
        decoded = String::from_utf8(bytes).ok()?;
        decoded.as_str()
    } else {
        text
    };
    if let Some(path) = text.strip_prefix("~/") {
        Some(std::path::PathBuf::from(std::env::var_os("HOME")?).join(path))
    } else {
        Some(text.into())
    }
}

/// `Some(is_file)` for an existing path, `None` for a missing or unusable one.
fn metadata_kind(path: &std::path::Path) -> Option<bool> {
    std::fs::metadata(path)
        .ok()
        .map(|metadata| metadata.is_file())
}

/// Filesystem checks of one parsing pass, each distinct path probed once. The
/// completeness check re-derives the candidates of every prefix at every
/// separator; without the cache a long keystroke drop cost O(S²) `stat` calls on
/// the UI thread, with it the probes grow linearly with the input.
struct Probe<F: FnMut(&std::path::Path) -> Option<bool>> {
    stat: F,
    cache: std::collections::HashMap<String, Option<bool>>,
}

impl<F: FnMut(&std::path::Path) -> Option<bool>> Probe<F> {
    fn new(stat: F) -> Self {
        Self {
            stat,
            cache: std::collections::HashMap::new(),
        }
    }

    fn kind(&mut self, raw: &str) -> Option<bool> {
        if let Some(kind) = self.cache.get(raw) {
            return *kind;
        }
        let kind = local_path(raw).and_then(|path| (self.stat)(&path));
        self.cache.insert(raw.to_owned(), kind);
        kind
    }

    fn exists(&mut self, raw: &str) -> bool {
        self.kind(raw).is_some()
    }

    fn is_file(&mut self, raw: &str) -> bool {
        self.kind(raw) == Some(true)
    }

    fn candidates(&mut self, raw: &str, backslash_escapes: bool) -> Vec<String> {
        let mut candidates = Vec::new();
        // Not str::lines(): Terminal.app sends a multi-line paste with bare CR, which
        // lines() does not split, so a pasted list became one path with spaces.
        for line in raw
            .split(['\r', '\n'])
            .map(str::trim)
            .filter(|line| !line.is_empty())
        {
            let paths = if self.exists(line) {
                vec![line.to_owned()]
            } else {
                split_paths(line, backslash_escapes)
            };
            for path in paths {
                // cmux may concatenate consecutive drops without any separator.
                // A slash AFTER an existing regular file cannot be part of that
                // file's path, so it unambiguously starts the next absolute path.
                let mut start = 0;
                for (index, character) in path.char_indices() {
                    if character == '/' && index > start && self.is_file(&path[start..index]) {
                        candidates.push(path[start..index].to_owned());
                        start = index;
                    }
                }
                candidates.push(path[start..].to_owned());
            }
        }
        candidates
    }

    fn valid(&mut self, text: &str, backslash_escapes: bool) -> bool {
        let paths = self.candidates(text, backslash_escapes);
        !paths.is_empty() && paths.iter().all(|path| self.exists(path))
    }

    fn boundary(&mut self, raw: &str, backslash_escapes: bool) -> Option<usize> {
        let mut lexer = ShellLexer::new(backslash_escapes);
        let mut boundary = None;
        for (index, ch) in raw.char_indices() {
            let separator = match lexer.next(ch) {
                Token::Separator => true,
                Token::Char('/') => false,
                _ => continue,
            };
            let prefix = raw[..index].trim();
            let concatenated = !separator
                && self
                    .candidates(prefix, backslash_escapes)
                    .last()
                    .is_some_and(|path| self.is_file(path));
            if (separator || concatenated) && self.valid(prefix, backslash_escapes) {
                boundary = Some(index);
            }
        }
        if !lexer.open() && self.valid(raw, backslash_escapes) {
            Some(raw.len())
        } else {
            boundary
        }
    }
}

/// Граница принятого raw-drop: хвост возвращается без перевычисления экранирования.
pub(crate) fn complete_prefix(raw: &str) -> Option<usize> {
    prefix_boundary(raw, BACKSLASH_ESCAPES)
}

fn prefix_boundary(raw: &str, backslash_escapes: bool) -> Option<usize> {
    Probe::new(metadata_kind).boundary(raw, backslash_escapes)
}

pub(crate) fn input_candidates(raw: &str) -> Vec<String> {
    candidates(raw, BACKSLASH_ESCAPES)
}

fn candidates(raw: &str, backslash_escapes: bool) -> Vec<String> {
    Probe::new(metadata_kind).candidates(raw, backslash_escapes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pasted_lines_split_on_bare_cr_like_on_lf() {
        let directory =
            std::env::temp_dir().join(format!("gigaam cr paste {}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let paths: Vec<String> = ["Лекция — речь.wav", "Планёрка 24.09.m4a"]
            .iter()
            .map(|name| {
                let path = directory.join(name);
                std::fs::write(&path, []).unwrap();
                path.to_string_lossy().into_owned()
            })
            .collect();
        for separator in ["\r", "\n", "\r\n"] {
            assert_eq!(
                input_candidates(&paths.join(separator)),
                paths,
                "{separator:?}"
            );
        }
        std::fs::remove_dir_all(&directory).unwrap();
    }

    #[test]
    fn quoted_single_path_is_decoded_before_worker_submission() {
        let path = std::env::temp_dir().join(format!("gigaam quoted {}.wav", std::process::id()));
        std::fs::write(&path, []).unwrap();
        let text = path.to_string_lossy().to_string();
        for raw in [format!("\"{text}\""), format!("'{text}'")] {
            assert_eq!(input_candidates(&raw), vec![text.clone()]);
        }
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn literal_quotes_in_existing_filename_are_preserved() {
        let path = std::env::temp_dir().join(format!("gigaam 'quoted' {}.wav", std::process::id()));
        std::fs::write(&path, []).unwrap();
        let text = path.to_string_lossy().to_string();
        assert_eq!(input_candidates(&text), vec![text.clone()]);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn directory_with_spaces_is_one_candidate_not_a_concatenation_boundary() {
        let directory = std::env::temp_dir().join(format!("gigaam input {}", std::process::id()));
        std::fs::create_dir_all(directory.join("nested")).unwrap();
        let path = directory.join("nested").to_string_lossy().to_string();
        assert_eq!(input_candidates(&path), vec![path]);
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn backslash_escapes_only_outside_quotes_and_never_on_windows() {
        // Windows: a backslash is the path separator, never an escape.
        assert_eq!(
            split_paths(r#""C:\My Files\a.wav" C:\Users\b.wav"#, false),
            vec![r"C:\My Files\a.wav", r"C:\Users\b.wav"]
        );
        // POSIX shells: an escape outside quotes only.
        assert_eq!(
            split_paths(r#""/tmp/a\b.wav" '/tmp/c\ d.wav' /tmp/e\ f.wav"#, true),
            vec![r"/tmp/a\b.wav", r"/tmp/c\ d.wav", "/tmp/e f.wav"]
        );
        assert_eq!(
            split_paths(r"/tmp/trailing\", true),
            vec![r"/tmp/trailing\"]
        );
    }

    #[test]
    fn quoted_backslash_path_is_complete_on_every_platform() {
        let directory = std::env::temp_dir().join(format!("gigaam-bs-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        // A file name with a literal backslash is legal on Unix; Windows can't create
        // it, but the parsing rule under test is platform-independent there too.
        let Ok(()) = std::fs::write(directory.join(r"a\b.wav"), []) else {
            return;
        };
        let path = format!(r"{}/a\b.wav", directory.display());
        let quoted = format!("\"{path}\"");
        assert_eq!(prefix_boundary(&quoted, true), Some(quoted.len()));
        assert_eq!(candidates(&quoted, true), vec![path.clone()]);
        assert_eq!(prefix_boundary(&path, false), Some(path.len()));
        std::fs::remove_dir_all(&directory).unwrap();
    }

    #[test]
    fn completion_probes_each_path_a_bounded_number_of_times() {
        // Every separator re-validated the whole prefix, so a long keystroke drop
        // cost O(S²) `stat` calls on the UI thread.
        let words: Vec<String> = (0..300).map(|i| format!("/missing/w{i}")).collect();
        let raw = words.join(" ");
        let mut probes = 0;
        let mut probe = Probe::new(|_: &std::path::Path| {
            probes += 1;
            None
        });
        assert_eq!(probe.boundary(&raw, true), None);
        drop(probe);
        assert!(probes <= 4 * words.len(), "{probes} probes");
    }

    #[test]
    fn editing_keeps_utf8_boundaries_and_original_characters() {
        let mut input = InputState::default();
        input.open(InputMode::Paths, "а🦄б".into());
        input.left();
        input.backspace();
        assert_eq!(input.text(), "аб");
        input.insert("\u{a0}");
        assert_eq!(input.text(), "а\u{a0}б");
        input.home();
        input.delete();
        assert_eq!(input.text(), "\u{a0}б");
        input.end();
        input.right();
        assert_eq!(input.cursor(), input.text().len());
        input.clear();
        assert_eq!(input.mode, InputMode::Paths);
        input.backspace();
        input.delete();
        assert_eq!(input.cursor(), 0);
        input.close();
        assert_eq!(input.mode, InputMode::Hidden);
    }

    #[test]
    fn replace_positions_cursor_and_insertion_does_not_change_argument_mode() {
        let mut input = InputState::default();
        input.open(InputMode::Argument("/output"), "/output ".into());
        input.insert("/tmp/запись");
        input.home();
        input.right();
        input.insert("x");
        assert_eq!(input.text(), "/xoutput /tmp/запись");
        input.replace("/output /tmp".into());
        assert_eq!(input.cursor(), input.text().len());
        assert_eq!(input.mode, InputMode::Argument("/output"));
    }
}
