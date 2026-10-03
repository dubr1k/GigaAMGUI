//! Termination signals become an orderly shutdown instead of an instant death.
//!
//! The worker runs in its own process group (its own Job on Windows), so a
//! SIGTERM (kill, logout) or a SIGHUP (the terminal window closed) that killed
//! the TUI outright ran no destructor: `WorkerRuntime::drop` never stopped the
//! group and the worker with its LLM CLI children kept running as orphans. The
//! handler only records the signal; the main loop notices it within one poll
//! tick and leaves through the normal path (terminal restored, worker tree
//! stopped), then the signal is re-raised so the parent still sees how the
//! process ended. A second signal while that shutdown is stuck terminates at once.
//!
//! Windows needs none of this: the worker's Job has KILL_ON_JOB_CLOSE, so the
//! tree dies with the TUI's handle.

use std::sync::atomic::{AtomicI32, Ordering};

/// The first termination signal received, 0 for none.
static RECEIVED: AtomicI32 = AtomicI32::new(0);

#[cfg(unix)]
extern "C" fn on_signal(signal: libc::c_int) {
    // Async-signal-safe work only: an atomic swap, signal(2) and raise(3).
    if RECEIVED.swap(signal, Ordering::SeqCst) != 0 {
        unsafe {
            libc::signal(signal, libc::SIG_DFL);
            libc::raise(signal);
        }
    }
}

/// Routes SIGTERM, SIGHUP and SIGINT to [`requested`]. In raw mode Ctrl+C is a
/// key, so SIGINT here only comes from `kill -INT`.
pub(crate) fn install() {
    #[cfg(unix)]
    for signal in [libc::SIGTERM, libc::SIGHUP, libc::SIGINT] {
        // SAFETY: a zeroed sigaction with an `extern "C"` handler that touches only
        // an atomic and async-signal-safe libc calls; no Rust state is shared.
        unsafe {
            let mut action: libc::sigaction = std::mem::zeroed();
            action.sa_sigaction = on_signal as extern "C" fn(libc::c_int) as libc::sighandler_t;
            libc::sigemptyset(&mut action.sa_mask);
            action.sa_flags = libc::SA_RESTART;
            libc::sigaction(signal, &action, std::ptr::null_mut());
        }
    }
}

/// Whether a termination signal asked the process to shut down.
pub(crate) fn requested() -> bool {
    RECEIVED.load(Ordering::SeqCst) != 0
}

/// After the orderly shutdown: end the process by the signal that asked for it,
/// so a supervisor or shell sees the conventional status (e.g. 143 for SIGTERM).
pub(crate) fn reraise() {
    #[cfg(unix)]
    {
        let signal = RECEIVED.load(Ordering::SeqCst);
        if signal != 0 {
            // SAFETY: restores the default disposition, then delivers the signal.
            unsafe {
                libc::signal(signal, libc::SIG_DFL);
                libc::raise(signal);
            }
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn a_signal_is_recorded_not_fatal() {
        // The handler itself, as the kernel would call it; no signal is sent, so
        // the test process and its other threads are unaffected.
        assert!(!requested());
        on_signal(libc::SIGTERM);
        assert!(requested());
        RECEIVED.store(0, Ordering::SeqCst);
    }
}
