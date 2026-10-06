use crossterm::{
    cursor::Show,
    event::{DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use std::{
    io::{self, Stdout, Write},
    sync::atomic::{AtomicBool, Ordering},
};

/// Set while the real terminal is in TUI mode. Whoever clears it restores the
/// terminal: the panic hook (before the message is printed) or the guard's Drop.
static TERMINAL_ACTIVE: AtomicBool = AtomicBool::new(false);

pub(crate) struct TerminalGuard<
    W: Write = Stdout,
    R: FnMut() -> io::Result<()> = fn() -> io::Result<()>,
> {
    writer: W,
    leave_raw: R,
    raw: bool,
    alternate: bool,
    paste: bool,
    mouse: bool,
    /// The process terminal, shared with the panic hook through `TERMINAL_ACTIVE`.
    process_terminal: bool,
}

impl TerminalGuard {
    pub(crate) fn enter(mouse: bool) -> io::Result<Self> {
        let mut guard = Self::activate(
            io::stdout(),
            mouse,
            enable_raw_mode,
            disable_raw_mode as fn() -> io::Result<()>,
        )?;
        guard.process_terminal = true;
        TERMINAL_ACTIVE.store(true, Ordering::SeqCst);
        Ok(guard)
    }
}

/// Every capability the TUI may have turned on, each in its own call so that one
/// failed write never skips the rest. Disabling one that is off is harmless.
fn restore_all(writer: &mut impl Write, leave_raw: impl FnOnce() -> io::Result<()>) {
    let _ = execute!(writer, Show);
    let _ = execute!(writer, DisableMouseCapture);
    let _ = execute!(writer, DisableBracketedPaste);
    let _ = execute!(writer, LeaveAlternateScreen);
    let _ = leave_raw();
}

/// The body of the panic hook. The default report goes to stderr, which during a
/// session is the alternate screen in raw mode: the message was drawn over the UI
/// and wiped by `LeaveAlternateScreen` in the guard's Drop, so a crash left no
/// trace. On the UI thread the terminal is restored first; a panicking helper
/// thread must not tear the terminal down under a UI that keeps running.
fn report_panic(
    active: &AtomicBool,
    ui_thread: bool,
    restore: impl FnOnce(),
    report: impl FnOnce(),
) {
    if ui_thread && active.swap(false, Ordering::SeqCst) {
        restore();
    }
    report();
}

/// Installed once before the terminal is entered; chains to the previous hook.
pub(crate) fn install_panic_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        report_panic(
            &TERMINAL_ACTIVE,
            std::thread::current().name() == Some("main"),
            || restore_all(&mut io::stdout(), disable_raw_mode),
            || previous(info),
        );
    }));
}

impl<W: Write, R: FnMut() -> io::Result<()>> TerminalGuard<W, R> {
    fn activate(
        writer: W,
        mouse: bool,
        enter_raw: impl FnOnce() -> io::Result<()>,
        leave_raw: R,
    ) -> io::Result<Self> {
        let mut guard = Self {
            writer,
            leave_raw,
            raw: false,
            alternate: false,
            paste: false,
            mouse: false,
            process_terminal: false,
        };
        enter_raw()?;
        guard.raw = true;
        // Record attempted activation first: a failed Write may have sent a prefix.
        guard.alternate = true;
        execute!(guard.writer, EnterAlternateScreen)?;
        guard.paste = true;
        execute!(guard.writer, EnableBracketedPaste)?;
        if mouse {
            guard.set_mouse(true)?;
        }
        Ok(guard)
    }

    pub(crate) fn set_mouse(&mut self, enabled: bool) -> io::Result<()> {
        if enabled {
            self.mouse = true;
            execute!(self.writer, EnableMouseCapture)
        } else {
            execute!(self.writer, DisableMouseCapture)?;
            self.mouse = false;
            Ok(())
        }
    }
}

impl<W: Write, R: FnMut() -> io::Result<()>> Drop for TerminalGuard<W, R> {
    fn drop(&mut self) {
        // The panic hook already restored the process terminal: a second
        // LeaveAlternateScreen would restore the cursor saved on entry and let the
        // shell prompt overwrite the panic report.
        if self.process_terminal && !TERMINAL_ACTIVE.swap(false, Ordering::SeqCst) {
            return;
        }
        // Separate calls: a broken output capability must not skip later cleanup.
        let _ = execute!(self.writer, Show);
        if self.mouse {
            let _ = execute!(self.writer, DisableMouseCapture);
        }
        if self.paste {
            let _ = execute!(self.writer, DisableBracketedPaste);
        }
        if self.alternate {
            let _ = execute!(self.writer, LeaveAlternateScreen);
        }
        if self.raw {
            let _ = (self.leave_raw)();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    };

    #[derive(Clone, Default)]
    struct Sink {
        bytes: Arc<Mutex<Vec<u8>>>,
        fail_once: Arc<AtomicBool>,
    }
    impl std::io::Write for Sink {
        fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
            if self.fail_once.swap(false, Ordering::SeqCst) {
                return Err(std::io::Error::other("fixture write failed"));
            }
            self.bytes.lock().unwrap().extend_from_slice(data);
            Ok(data.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    impl Sink {
        fn text(&self) -> String {
            String::from_utf8(self.bytes.lock().unwrap().clone()).unwrap()
        }
    }

    #[test]
    fn panic_unwind_restores_all_terminal_capabilities() {
        let sink = Sink::default();
        let restored = Arc::new(AtomicBool::new(false));
        let raw = restored.clone();
        let output = sink.clone();
        let result = std::panic::catch_unwind(move || {
            let _guard = TerminalGuard::activate(
                output,
                true,
                || Ok(()),
                move || {
                    raw.store(true, Ordering::SeqCst);
                    Ok(())
                },
            )
            .unwrap();
            panic!("controlled unwind");
        });
        assert!(result.is_err());
        assert!(restored.load(Ordering::SeqCst));
        let text = sink.text();
        for escape in [
            "\x1b[?1049h",
            "\x1b[?1049l",
            "\x1b[?2004h",
            "\x1b[?2004l",
            "\x1b[?1006l",
            "\x1b[?25h",
        ] {
            assert!(text.contains(escape), "missing {escape:?}: {text:?}");
        }
    }

    #[test]
    fn a_cleanup_write_failure_does_not_skip_remaining_cleanup() {
        let sink = Sink::default();
        let restored = Arc::new(AtomicBool::new(false));
        let raw = restored.clone();
        let guard = TerminalGuard::activate(
            sink.clone(),
            true,
            || Ok(()),
            move || {
                raw.store(true, Ordering::SeqCst);
                Ok(())
            },
        )
        .unwrap();
        sink.fail_once.store(true, Ordering::SeqCst);
        drop(guard);
        let text = sink.text();
        assert!(text.contains("\x1b[?1049l"));
        assert!(text.contains("\x1b[?2004l"));
        assert!(restored.load(Ordering::SeqCst));
    }

    #[test]
    fn a_ui_thread_panic_restores_the_terminal_before_the_report_once() {
        let active = AtomicBool::new(true);
        let events = Mutex::new(Vec::new());
        report_panic(
            &active,
            true,
            || events.lock().unwrap().push("restore"),
            || events.lock().unwrap().push("report"),
        );
        assert_eq!(*events.lock().unwrap(), ["restore", "report"]);
        assert!(
            !active.load(Ordering::SeqCst),
            "Drop must not restore again"
        );
        report_panic(
            &active,
            true,
            || events.lock().unwrap().push("restore"),
            || events.lock().unwrap().push("report"),
        );
        assert_eq!(*events.lock().unwrap(), ["restore", "report", "report"]);
    }

    #[test]
    fn a_helper_thread_panic_leaves_the_running_ui_alone() {
        let active = AtomicBool::new(true);
        let restored = AtomicBool::new(false);
        let reported = AtomicBool::new(false);
        report_panic(
            &active,
            false,
            || restored.store(true, Ordering::SeqCst),
            || reported.store(true, Ordering::SeqCst),
        );
        assert!(!restored.load(Ordering::SeqCst));
        assert!(reported.load(Ordering::SeqCst));
        assert!(active.load(Ordering::SeqCst));
    }

    #[test]
    fn restore_all_disables_every_capability_and_leaves_raw_mode() {
        let mut sink = Sink::default();
        let raw = AtomicBool::new(false);
        restore_all(&mut sink, || {
            raw.store(true, Ordering::SeqCst);
            Ok(())
        });
        let text = sink.text();
        for escape in ["\x1b[?1049l", "\x1b[?2004l", "\x1b[?1006l", "\x1b[?25h"] {
            assert!(text.contains(escape), "missing {escape:?}: {text:?}");
        }
        assert!(raw.load(Ordering::SeqCst));
    }

    #[test]
    fn failed_activation_restores_raw_mode() {
        let sink = Sink::default();
        sink.fail_once.store(true, Ordering::SeqCst);
        let restored = Arc::new(AtomicBool::new(false));
        let raw = restored.clone();
        let result = TerminalGuard::activate(
            sink.clone(),
            true,
            || Ok(()),
            move || {
                raw.store(true, Ordering::SeqCst);
                Ok(())
            },
        );
        assert!(result.is_err());
        assert!(restored.load(Ordering::SeqCst));
        assert!(sink.text().contains("\x1b[?1049l"));
    }
}
