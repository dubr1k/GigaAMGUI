import Foundation

/// What a live worker `error` event means for the session.
///
/// The worker answers a rejected single command — a second pause, a question
/// before the first final, cancelling an answer that already finished — with the
/// same `error` event as a failed `live_start`. A current worker names the
/// command in `"command"`; an older one sends only the text
/// (src/services/live_worker_service.py), which is then all there is to go on.
public enum LiveErrorDisposition: Equatable {
    /// The session is over (or never started): end the job with this error.
    case fatal
    /// The assistant will not answer this question; show the reason in its place.
    case questionRejected
    /// A harmless race, e.g. cancelling an answer that has just completed.
    case ignorable
    /// A rejected command; the session goes on. Worth a log line, not the status.
    case logged
}

public enum LiveErrorPolicy {
    /// The worker has no session: after live_stop this is the stop's own answer,
    /// otherwise the session is gone without a live_stopped. Either way nothing
    /// more will come.
    public static let notRunning = "No live session is running"
    public static let nothingToCancel = "No assistant question is running"
    /// live_ask rejections that answer the question the user just asked.
    public static let questionRejections: Set<String> = [
        "Question is required",
        "No final transcript events are available yet",
        "LLM settings are required",
    ]

    /// `command` is the event's `"command"`, nil from a worker that does not send it.
    public static func disposition(of message: String, command: String?, sessionReported: Bool) -> LiveErrorDisposition {
        guard let command else { return disposition(ofUnnamed: message, sessionReported: sessionReported) }
        // `notRunning` describes the session, not the command that drew it: the
        // worker will send nothing more, so waiting for live_stopped would leave
        // the page recording forever. The one text a named error is read by.
        if message == notRunning { return .fatal }
        switch command {
        // The session never started, or the stop failed before its live_stopped.
        case "live_start", "live_stop": return .fatal
        // Every refusal of live_ask answers the question just asked, whatever the
        // wording — including «already running», which by text was only logged.
        case "live_ask": return .questionRejected
        // Nothing to cancel: the answer finished first.
        case "live_ask_cancel": return .ignorable
        // live_pause/live_resume (a second click), a bad live_audio chunk, an
        // unknown capture event kind, a command newer than this client: notices.
        default: return .logged
        }
    }

    /// An older worker: the error does not say which command it answers.
    private static func disposition(ofUnnamed message: String, sessionReported: Bool) -> LiveErrorDisposition {
        // Until the first live_status the only command sent was live_start.
        guard sessionReported else { return .fatal }
        if message == notRunning { return .fatal }
        if message == nothingToCancel { return .ignorable }
        if questionRejections.contains(message) { return .questionRejected }
        return .logged
    }
}
