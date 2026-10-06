import Testing
@testable import GigaAMLiquidCore

/// Errors of a worker that names the command they answer (`"command": "live_…"`).
@Suite struct LiveErrorPolicyCommandTests {
    private func disposition(_ message: String, _ command: String, sessionReported: Bool = true) -> LiveErrorDisposition {
        LiveErrorPolicy.disposition(of: message, command: command, sessionReported: sessionReported)
    }

    @Test func aRejectedStartEndsTheJob() {
        #expect(disposition("Could not load the recognition model", "live_start", sessionReported: false) == .fatal)
        #expect(disposition("Processing is already running", "live_start", sessionReported: false) == .fatal)
        #expect(disposition("live_start failed: boom", "live_start") == .fatal)
    }

    /// The stop's own error means no live_stopped is coming.
    @Test func aRejectedStopEndsTheJob() {
        #expect(disposition("No live session is running", "live_stop") == .fatal)
        #expect(disposition("live_stop failed: boom", "live_stop") == .fatal)
    }

    /// Whatever the wording, an error for live_ask answers the question just asked;
    /// by text, "already running" was only logged and the answer field kept
    /// saying "the assistant is answering…".
    @Test func everyAskErrorAnswersTheQuestion() {
        #expect(disposition("An assistant question is already running", "live_ask") == .questionRejected)
        #expect(disposition("No final transcript events are available yet", "live_ask") == .questionRejected)
        #expect(disposition("Ask after the first phrase", "live_ask") == .questionRejected)
    }

    @Test func aCancelThatFoundNothingIsIgnored() {
        #expect(disposition("No assistant question is running", "live_ask_cancel") == .ignorable)
        #expect(disposition("Nothing to cancel", "live_ask_cancel") == .ignorable)
    }

    /// Pause/resume, a bad chunk, an unknown capture event kind: notices, also
    /// before the first live_status, where the old rule took any error for a
    /// rejected start.
    @Test func otherCommandsAreNotices() {
        #expect(disposition("Session is already paused", "live_pause") == .logged)
        #expect(disposition("Session is not paused", "live_resume") == .logged)
        #expect(disposition("Session is not recording", "live_pause", sessionReported: false) == .logged)
        #expect(disposition("live_audio pcm is not valid base64 int16", "live_audio") == .logged)
        #expect(disposition("Unknown capture event kind: 'x'", "live_capture_event") == .logged)
        #expect(disposition("Unknown command: 'live_mark'", "live_mark") == .logged)
    }

    /// "No session" is about the session, not the command: nothing more will
    /// come from the worker, so waiting for live_stopped would hang the page.
    @Test func aMissingSessionEndsTheJobWhateverTheCommand() {
        #expect(disposition("No live session is running", "live_pause") == .fatal)
        #expect(disposition("No live session is running", "live_audio") == .fatal)
        #expect(disposition("No live session is running", "live_ask") == .fatal)
    }
}

/// An older worker sends `error` without `command`: its text is all there is.
@Suite struct LiveErrorPolicyLegacyTests {
    private func disposition(_ message: String, sessionReported: Bool) -> LiveErrorDisposition {
        LiveErrorPolicy.disposition(of: message, command: nil, sessionReported: sessionReported)
    }

    @Test func anyErrorBeforeTheFirstStatusRejectsTheStart() {
        #expect(disposition("Could not load the recognition model", sessionReported: false) == .fatal)
        #expect(disposition("Unknown command: 'live_start'", sessionReported: false) == .fatal)
    }

    @Test func aRejectedCommandDoesNotEndTheSession() {
        // A second pause, a bad chunk, a late capture event while stopping: the
        // session (and its coming live_stopped) must survive them.
        #expect(disposition("Session is already paused", sessionReported: true) == .logged)
        #expect(disposition("live_audio pcm is not valid base64 int16", sessionReported: true) == .logged)
        #expect(disposition("An assistant question is already running", sessionReported: true) == .logged)
    }

    @Test func questionRejectionsAnswerTheQuestion() {
        #expect(disposition("No final transcript events are available yet", sessionReported: true) == .questionRejected)
        #expect(disposition("Question is required", sessionReported: true) == .questionRejected)
    }

    @Test func cancellingAFinishedAnswerIsIgnored() {
        #expect(disposition("No assistant question is running", sessionReported: true) == .ignorable)
    }

    @Test func aMissingSessionEndsTheJob() {
        #expect(disposition("No live session is running", sessionReported: true) == .fatal)
    }
}
