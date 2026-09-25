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

pub(crate) fn split_shell_paths(raw: &str) -> Vec<String> {
    let mut paths = Vec::new();
    let mut current = String::new();
    let mut quote = None;
    let mut escaped = false;
    for character in raw.chars() {
        if escaped {
            current.push(character);
            escaped = false;
        } else if character == '\\' {
            escaped = true;
        } else if matches!(character, '\'' | '"') {
            if quote == Some(character) {
                quote = None;
            } else if quote.is_none() {
                quote = Some(character);
            } else {
                current.push(character);
            }
        // Finder uses NBSP before «— копия». Shells do not treat Unicode
        // spaces as separators, so preserve them as part of the filename.
        } else if matches!(character, ' ' | '\t' | '\r' | '\n') && quote.is_none() {
            if !current.is_empty() {
                paths.push(std::mem::take(&mut current));
            }
        } else {
            current.push(character);
        }
    }
    if escaped {
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

/// Граница принятого raw-drop: хвост возвращается без перевычисления экранирования.
pub(crate) fn complete_prefix(raw: &str) -> Option<usize> {
    let valid = |text: &str| {
        let paths = input_candidates(text);
        !paths.is_empty()
            && paths
                .iter()
                .all(|p| local_path(p).is_some_and(|p| p.exists()))
    };
    let mut quote = None;
    let mut escaped = false;
    let mut boundary = None;
    for (index, ch) in raw.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        if ch == '\\' {
            escaped = true;
            continue;
        }
        if matches!(ch, '\'' | '"') {
            if quote == Some(ch) {
                quote = None;
            } else if quote.is_none() {
                quote = Some(ch);
            }
            continue;
        }
        let prefix = raw[..index].trim();
        let separator = quote.is_none() && matches!(ch, ' ' | '\t' | '\r' | '\n');
        let concatenated = ch == '/'
            && input_candidates(prefix)
                .last()
                .is_some_and(|p| local_path(p).is_some_and(|p| p.is_file()));
        if (separator || concatenated) && valid(prefix) {
            boundary = Some(index);
        }
    }
    if quote.is_none() && !escaped && valid(raw) {
        Some(raw.len())
    } else {
        boundary
    }
}

pub(crate) fn input_candidates(raw: &str) -> Vec<String> {
    let mut candidates = Vec::new();
    for line in raw.lines().map(str::trim).filter(|line| !line.is_empty()) {
        let paths = if local_path(line).is_some_and(|path| path.exists()) {
            vec![line.to_owned()]
        } else {
            split_shell_paths(line)
        };
        for path in paths {
            // cmux may concatenate consecutive drops without any separator.
            // A slash AFTER an existing regular file cannot be part of that
            // file's path, so it unambiguously starts the next absolute path.
            let mut start = 0;
            for (index, character) in path.char_indices() {
                if character == '/'
                    && index > start
                    && local_path(&path[start..index]).is_some_and(|p| p.is_file())
                {
                    candidates.push(path[start..index].to_owned());
                    start = index;
                }
            }
            candidates.push(path[start..].to_owned());
        }
    }
    candidates
}

#[cfg(test)]
mod tests {
    use super::*;

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
