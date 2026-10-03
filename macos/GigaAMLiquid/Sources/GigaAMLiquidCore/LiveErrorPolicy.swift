import Foundation

/// What a live worker `error` event means for the session.
///
/// The worker answers a rejected single command — a second pause, a question
/// before the first final, cancelling an answer that already finished — with the
/// same `error` event as a failed `live_start`, and the event carries no command
/// id. Its text (src/services/live_worker_service.py) is all there is to go on.
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

    public static func disposition(of message: String, sessionReported: Bool) -> LiveErrorDisposition {
        // Until the first live_status the only command sent was live_start.
        guard sessionReported else { return .fatal }
        if message == notRunning { return .fatal }
        if message == nothingToCancel { return .ignorable }
        if questionRejections.contains(message) { return .questionRejected }
        return .logged
    }
}
