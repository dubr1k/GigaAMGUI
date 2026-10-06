//! Path helpers behind the input line: reading typed, pasted and dropped paths.

use std::{
    fs,
    path::{Path, PathBuf},
};

use crate::{
    app::{App, Page},
    i18n::{t, tf, Lang},
    input::{local_path, split_paths, InputMode, BACKSLASH_ESCAPES},
    results::canonical_path,
};

use super::*;

pub(crate) fn short_name(path: &str) -> String {
    path.rsplit(['/', '\\']).next().unwrap_or(path).to_string()
}

/// The folder of a queued file for display. `Path` splits on `\` too on Windows,
/// where splitting on `/` alone left the parent column empty.
pub(crate) fn parent_name(path: &str) -> &str {
    Path::new(path)
        .parent()
        .and_then(Path::to_str)
        .unwrap_or("")
}
/// Why a pasted path was rejected; [`PathError::message`] renders it in the UI
/// language (headless mode uses English).
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum PathError {
    HomeNotSet,
    Missing(String),
    NotAFile(String),
}

impl PathError {
    pub(crate) fn message(&self, lang: Lang) -> String {
        match self {
            PathError::HomeNotSet => t(lang, "err.home_not_set").to_owned(),
            PathError::Missing(path) => tf(lang, "err.file_missing", &[("path", path)]),
            PathError::NotAFile(path) => tf(lang, "err.not_a_file", &[("path", path)]),
        }
    }
}

/// The one reading of a path the user typed, pasted or passed on the command
/// line: a single shell-quoted or escaped token is unquoted (`My\ Dir`,
/// `"My Dir"`), a `file://` URL is percent-decoded and `~/` expands to the home
/// directory. Text that already names something on disk is taken literally, so
/// a real name with quotes or backslashes survives. `/output`, `/llm-file` and
/// headless mode all go through here; each used to have its own subset.
pub(crate) fn user_path(raw: &str) -> Result<PathBuf, PathError> {
    let text = raw.trim();
    if let Some(literal) = local_path(text).filter(|path| path.exists()) {
        return Ok(literal);
    }
    let parts = split_paths(text, BACKSLASH_ESCAPES);
    let text = match parts.as_slice() {
        [single] => single.as_str(),
        _ => text,
    };
    local_path(text).ok_or_else(|| {
        if text.starts_with("~/") {
            PathError::HomeNotSet
        } else {
            PathError::Missing(text.to_owned())
        }
    })
}

/// An existing regular file, canonical.
pub(crate) fn normalize_path(raw: &str) -> Result<String, PathError> {
    let path = user_path(raw)?;
    let path = canonical_path(&path).map_err(|_| PathError::Missing(path.display().to_string()))?;
    if !path.is_file() {
        return Err(PathError::NotAFile(path.display().to_string()));
    }
    Ok(path.to_string_lossy().into_owned())
}

/// A results directory (`/output`, `--output`): created when missing, canonical.
/// The error is the technical cause; callers add the sentence around it.
pub(crate) fn prepare_output_dir(raw: &str, lang: Lang) -> Result<String, String> {
    let path = user_path(raw).map_err(|error| error.message(lang))?;
    fs::create_dir_all(&path).map_err(|error| format!("{}: {error}", path.display()))?;
    let path = canonical_path(&path).map_err(|error| format!("{}: {error}", path.display()))?;
    Ok(path.to_string_lossy().into_owned())
}

/// Вставка — законченный блок, поэтому не ждёт Enter и не склеивается со следующей.
pub(crate) fn paste_input(app: &mut App, text: &str) {
    if text.trim().is_empty()
        || app.running()
        || app.rerun_confirmation.is_some()
        || app.stop_confirmation
        || app.help_open
        || app.show_path
        || app.results_open
    {
        return;
    }
    if app.input.mode == InputMode::Hidden {
        app.input.open(InputMode::Paths, String::new());
    }
    app.input.insert(text);
    app.selected_command = 0;
    if app.page == Page::Processing
        && app.command_menu.is_none()
        && !matches!(app.input.mode, InputMode::Argument(_))
        && !is_command(app.input.trim())
    {
        let raw = app.input.text().to_owned();
        queue_paths(app, &raw);
    }
}

/// Keystroke-drop: после паузы передаём только существующий завершённый префикс.
pub(crate) fn queue_complete_input(app: &mut App) {
    if app.running()
        || app.rerun_confirmation.is_some()
        || app.stop_confirmation
        || app.help_open
        || app.show_path
        || app.results_open
        || app.page != Page::Processing
        || app.command_menu.is_some()
        || matches!(app.input.mode, InputMode::Argument(_))
        || is_command(app.input.trim())
    {
        return;
    }
    let raw = app.input.text().to_owned();
    let Some(boundary) = crate::input::complete_prefix(&raw) else {
        return;
    };
    if app.submit_paths(raw[..boundary].trim().to_owned()) {
        let tail = raw[boundary..]
            .trim_start_matches([' ', '\t', '\r', '\n'])
            .to_owned();
        if tail.is_empty() {
            app.input.close();
        } else {
            app.input.open(InputMode::Paths, tail);
        }
    }
}

pub(crate) fn queue_paths(app: &mut App, raw: &str) {
    if app.submit_paths(raw.to_owned()) {
        app.input.close();
    }
}

pub(crate) fn complete_path(raw: &str) -> Option<String> {
    let text = raw.trim().trim_matches(['\'', '"']);
    if text.is_empty() || text.starts_with('/') && is_command(text) {
        return None;
    }
    let expanded = if let Some(path) = text.strip_prefix("~/") {
        format!("{}/{}", std::env::var("HOME").ok()?, path)
    } else {
        text.to_string()
    };
    let path = Path::new(&expanded);
    let (parent, prefix) = if expanded.ends_with('/') {
        (PathBuf::from(&expanded), "")
    } else {
        let parent = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        (parent.to_path_buf(), path.file_name()?.to_str()?)
    };
    let mut matches: Vec<PathBuf> = fs::read_dir(parent)
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with(prefix))
        })
        .collect();
    matches.sort();
    let candidate = matches.first()?;
    let mut completed = candidate.to_string_lossy().into_owned();
    if candidate.is_dir() {
        completed.push('/');
    }
    Some(completed)
}
