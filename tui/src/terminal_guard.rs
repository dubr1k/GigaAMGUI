use crossterm::{
    cursor::Show,
    event::{DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use std::io::{self, Stdout, Write};

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
}

impl TerminalGuard {
    pub(crate) fn enter(mouse: bool) -> io::Result<Self> {
        Self::activate(
            io::stdout(),
            mouse,
            enable_raw_mode,
            disable_raw_mode as fn() -> io::Result<()>,
        )
    }
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
