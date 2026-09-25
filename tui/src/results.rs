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

pub(crate) fn result_command(path: &Path, folder: bool) -> io::Result<Command> {
    let path = path.canonicalize()?;
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
                            return if status.success() {
                                Ok(())
                            } else {
                                Err(io::Error::other(format!("opener exited with {status}")))
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
    use crate::{app::dispatch, commands::clear_queue, ui::Action};

    #[test]
    fn opener_preserves_one_literal_path_and_rejects_missing_or_unsafe_targets() {
        let dir = crate::settings::isolated_config_dir();
        let path = dir.join("Запись ; & (1).TXT");
        std::fs::write(&path, "result").unwrap();
        let canonical = std::fs::canonicalize(&path).unwrap();
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
