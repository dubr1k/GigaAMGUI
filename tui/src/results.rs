//! Сохранённые файлы и безопасный запуск системного просмотрщика.
use crate::{
    app::App,
    i18n::{t, tf},
};
use std::{
    collections::HashSet,
    io,
    path::Path,
    process::{Command, Stdio},
    sync::mpsc,
};

/// `canonicalize` on Windows returns verbatim paths (`\\?\C:\…`). explorer.exe
/// does not understand them, and they look alien in the UI. The prefix is
/// dropped only for drive and UNC paths, where the plain form names the same
/// file; anything else keeps it.
pub(crate) fn without_verbatim_prefix(path: &str) -> String {
    if let Some(unc) = path.strip_prefix(r"\\?\UNC\") {
        return format!(r"\\{unc}");
    }
    match path.strip_prefix(r"\\?\") {
        Some(rest)
            if rest.as_bytes().get(1) == Some(&b':')
                && rest.as_bytes().first().is_some_and(u8::is_ascii_alphabetic) =>
        {
            rest.to_owned()
        }
        _ => path.to_owned(),
    }
}

/// `fs::canonicalize` without the Windows verbatim prefix.
pub(crate) fn canonical_path(path: &Path) -> io::Result<std::path::PathBuf> {
    let path = path.canonicalize()?;
    Ok(match path.to_str() {
        Some(text) if cfg!(windows) => without_verbatim_prefix(text).into(),
        _ => path,
    })
}

/// explorer.exe exits with 1 even after it opened the target and reports real
/// problems in its own window, so on Windows the exit code says nothing.
fn opener_failed(status: std::process::ExitStatus) -> bool {
    !cfg!(windows) && !status.success()
}

pub(crate) fn result_command(path: &Path, folder: bool) -> io::Result<Command> {
    let path = canonical_path(path)?;
    if !path.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "not a regular file",
        ));
    }
    if !folder
        && !matches!(
            path.extension()
                .and_then(|x| x.to_str())
                .map(str::to_ascii_lowercase)
                .as_deref(),
            Some("txt" | "md" | "srt" | "vtt")
        )
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "only text results can be opened",
        ));
    }
    let target = if folder {
        path.parent()
            .ok_or_else(|| io::Error::other("no parent directory"))?
    } else {
        &path
    };
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut c = Command::new("open");
        c.arg("--");
        c
    };
    #[cfg(target_os = "windows")]
    let mut command = Command::new("explorer.exe");
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let mut command = Command::new("xdg-open");
    command
        .arg(target)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    Ok(command)
}

impl App {
    pub(crate) fn saved_results(&self) -> Vec<String> {
        let mut seen = HashSet::new();
        self.result_files
            .iter()
            .chain(&self.llm_saved_files)
            .filter(|p| seen.insert(p.as_str()))
            .cloned()
            .collect()
    }

    pub(crate) fn open_selected_result(&mut self, folder: bool) {
        if self.result_opening.is_some() {
            return;
        }
        let Some(path) = self.saved_results().get(self.result_cursor).cloned() else {
            return;
        };
        let (sender, receiver) = mpsc::sync_channel(1);
        self.result_opening = Some(receiver);
        self.result_notice = t(self.lang, "results.opening").into();
        // Filesystem checks and the desktop opener must not stall terminal frames.
        std::thread::spawn(move || {
            let result = result_command(Path::new(&path), folder)
                .and_then(|mut command| {
                    let mut child = command.spawn()?;
                    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
                    loop {
                        if let Some(status) = child.try_wait()? {
                            return if opener_failed(status) {
                                Err(io::Error::other(format!("opener exited with {status}")))
                            } else {
                                Ok(())
                            };
                        }
                        if std::time::Instant::now() >= deadline {
                            // Некоторые просмотрщики живут, пока открыто окно. Reap отдельно.
                            std::thread::spawn(move || {
                                let _ = child.wait();
                            });
                            return Ok(());
                        }
                        std::thread::sleep(std::time::Duration::from_millis(25));
                    }
                })
                .map_err(|error| format!("{path}: {error}"));
            let _ = sender.send(result);
        });
    }

    pub(crate) fn poll_result_opening(&mut self) {
        let Some(receiver) = &self.result_opening else {
            return;
        };
        let result = match receiver.try_recv() {
            Ok(result) => result,
            Err(mpsc::TryRecvError::Empty) => return,
            Err(mpsc::TryRecvError::Disconnected) => {
                Err(t(self.lang, "status.unknown_error").into())
            }
        };
        self.result_opening = None;
        self.result_notice = match result {
            Ok(()) => t(self.lang, "results.opened").into(),
            Err(error) => tf(self.lang, "results.open_error", &[("error", &error)]),
        };
        self.status = self.result_notice.clone();
        self.log(self.status.clone());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{action::Action, app::dispatch, commands::clear_queue};

    #[test]
    fn opener_preserves_one_literal_path_and_rejects_missing_or_unsafe_targets() {
        let dir = crate::settings::isolated_config_dir();
        let path = dir.join("Запись ; & (1).TXT");
        std::fs::write(&path, "result").unwrap();
        let canonical = canonical_path(&path).unwrap();
        let command = result_command(&path, false).unwrap();
        assert!(command.get_args().any(|arg| arg == canonical.as_os_str()));
        assert!(result_command(&dir.join("missing.txt"), false).is_err());
        assert!(result_command(&dir, false).is_err());
        let unsafe_file = dir.join("program.sh");
        std::fs::write(&unsafe_file, "exit").unwrap();
        assert!(result_command(&unsafe_file, false).is_err());
        let folder = result_command(&path, true).unwrap();
        assert!(folder
            .get_args()
            .any(|arg| arg == canonical.parent().unwrap().as_os_str()));
    }

    #[test]
    fn verbatim_prefixes_are_removed_only_where_the_plain_form_means_the_same() {
        assert_eq!(
            without_verbatim_prefix(r"\\?\C:\Записи\a.txt"),
            r"C:\Записи\a.txt"
        );
        assert_eq!(
            without_verbatim_prefix(r"\\?\UNC\server\share\a.txt"),
            r"\\server\share\a.txt"
        );
        // Not a drive path: the prefix is load-bearing (device paths, volume GUIDs).
        assert_eq!(
            without_verbatim_prefix(r"\\?\Volume{1234}\a.txt"),
            r"\\?\Volume{1234}\a.txt"
        );
        assert_eq!(without_verbatim_prefix("/tmp/a.txt"), "/tmp/a.txt");
    }

    #[cfg(unix)]
    #[test]
    fn a_failing_opener_is_reported() {
        use std::os::unix::process::ExitStatusExt;
        assert!(opener_failed(std::process::ExitStatus::from_raw(1 << 8)));
        assert!(!opener_failed(std::process::ExitStatus::from_raw(0)));
    }

    #[cfg(windows)]
    #[test]
    fn explorer_exit_code_one_is_not_a_failure() {
        use std::os::windows::process::ExitStatusExt;
        assert!(!opener_failed(std::process::ExitStatus::from_raw(1)));
    }

    #[test]
    fn picker_keeps_all_results_after_clear_and_is_modal() {
        let mut app = crate::test_support::ready_app();
        app.result_files = vec!["/a.txt".into(), "/b.vtt".into()];
        app.llm_saved_files = vec!["/b.vtt".into(), "/summary.txt".into()];
        clear_queue(&mut app);
        assert_eq!(
            app.saved_results(),
            vec!["/a.txt", "/b.vtt", "/summary.txt"]
        );
        dispatch(&mut app, Action::ShowResults(true));
        dispatch(&mut app, Action::AddFiles);
        crate::commands::paste_input(&mut app, "/unwanted.wav");
        assert!(app.input.is_empty());
        assert!(app.pending_inputs.is_empty());
        dispatch(&mut app, Action::SelectResult(2));
        assert_eq!(app.result_cursor, 2);
        dispatch(&mut app, Action::ShowResults(false));
        assert!(!app.results_open);
        assert_eq!(app.saved_results().len(), 3);
    }
}
