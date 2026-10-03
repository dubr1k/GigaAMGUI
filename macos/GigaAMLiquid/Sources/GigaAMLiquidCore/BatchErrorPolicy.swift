import Foundation

/// What an `error` event means for a transcription batch (hello → start → …).
public enum BatchErrorDisposition: Equatable {
    /// The batch failed: end the job with this error.
    case fatal
    /// The worker refused `hello`; keep it for a failure report, not the log.
    case diagnostic
    /// Another command's error (a late reply to `cancel`, …); the batch goes on.
    case logged
}

public enum BatchErrorPolicy {
    /// `command` is the event's `"command"`, nil from a worker that does not send
    /// it; `helloReply` is whether this is the first event after `hello`.
    public static func disposition(command: String?, helloReply: Bool) -> BatchErrorDisposition {
        switch command {
        // Only the batch's own command can fail it.
        case "start": return .fatal
        case "hello": return .diagnostic
        // "Nothing is being processed" answers a `cancel` that crossed the
        // batch's end; it used to fail a batch whose files were still coming.
        case .some: return .logged
        // An older worker: the first reply after hello answers it (a worker older
        // than the handshake rejects it); anything later can only be the batch.
        case nil: return helloReply ? .diagnostic : .fatal
        }
    }
}
