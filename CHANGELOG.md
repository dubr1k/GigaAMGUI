# Changelog

## Unreleased

- TUI: versioned worker handshake, bounded asynchronous transport, actionable
  diagnostics and explicit reconnect without silently repeating a job.
- Large ASR/resolver/LLM responses no longer disappear at the diagnostic-line
  limit. Interactive ASR events omit transcript bodies; protocol messages above
  8 MiB fail explicitly. Cancelling a rejected startup no longer leaves a phantom job.
- One lifecycle state locks ASR/LLM immediately; confirmed force-stop owns and
  reaps the worker tree. CLI cancellation now includes descendants and reports
  unconfirmed termination instead of declaring successful cancellation.
- Terminal capabilities restore independently on normal/error/panic unwind.
  macOS runtime tests pass; Windows process ownership is compile-checked only.
- Separate batch/file progress, truthful completion/cancellation summaries and
  full saved-result picker (F9), with explicit shell-free file/folder opening.
- TUI: asynchronous recursive folder addition, canonical duplicate detection,
  correlated cancellation and protection against stale resolver responses.
- Stable queue selection, per-file removal and undo; clearing the queue preserves
  transcripts and LLM session results.
- Labelled Unicode-aware input editor; idle input is hidden. Repeated bracketed
  and raw terminal drops no longer accumulate in the input line.
- Pending-only default start, failed/selected run actions, confirmation before
  reprocessing completed items, and immediate startup locking.
- Selected-file path/details view, accessible queue actions and narrow-screen
  pet suppression; Russian and English labels and contextual keyboard handling.
- Regression coverage for worker protocol, queue state, input editing, rendering
  and real PTY screen state.
