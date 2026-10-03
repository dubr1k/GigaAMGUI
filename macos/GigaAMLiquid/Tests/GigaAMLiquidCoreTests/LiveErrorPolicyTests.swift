import Testing
@testable import GigaAMLiquidCore

@Suite struct LiveErrorPolicyTests {
    @Test func anyErrorBeforeTheFirstStatusRejectsTheStart() {
        #expect(LiveErrorPolicy.disposition(of: "Could not load the recognition model", sessionReported: false) == .fatal)
        #expect(LiveErrorPolicy.disposition(of: "Unknown command: 'live_start'", sessionReported: false) == .fatal)
    }

    @Test func aRejectedCommandDoesNotEndTheSession() {
        // A second pause, a bad chunk, a late capture event while stopping: the
        // session (and its coming live_stopped) must survive them.
        #expect(LiveErrorPolicy.disposition(of: "Session is already paused", sessionReported: true) == .logged)
        #expect(LiveErrorPolicy.disposition(of: "live_audio pcm is not valid base64 int16", sessionReported: true) == .logged)
        #expect(LiveErrorPolicy.disposition(of: "An assistant question is already running", sessionReported: true) == .logged)
    }

    @Test func questionRejectionsAnswerTheQuestion() {
        #expect(LiveErrorPolicy.disposition(of: "No final transcript events are available yet", sessionReported: true) == .questionRejected)
        #expect(LiveErrorPolicy.disposition(of: "Question is required", sessionReported: true) == .questionRejected)
    }

    @Test func cancellingAFinishedAnswerIsIgnored() {
        #expect(LiveErrorPolicy.disposition(of: "No assistant question is running", sessionReported: true) == .ignorable)
    }

    @Test func aMissingSessionEndsTheJob() {
        #expect(LiveErrorPolicy.disposition(of: "No live session is running", sessionReported: true) == .fatal)
    }
}
