import Testing
@testable import GigaAMLiquidCore

@Suite struct LiveCaptureNoticeTests {
    /// A silent loopback that resumes is the timeline catching up, not a problem;
    /// "idle gap=3.512s" replaced "Recording" in the status line every time.
    @Test func discontinuitiesAreLogOnly() {
        #expect(LiveCaptureNotice.classify(kind: "discontinuity", detail: "idle gap=3.512s") == .logOnly)
        #expect(LiveCaptureNotice.classify(kind: "discontinuity", detail: "seq gap: expected 5, got 7") == .logOnly)
        #expect(LiveCaptureNotice.classify(kind: "status", detail: "Session stopped with errors: export") == .logOnly)
    }

    /// The worker reports overflow as a dropped-frame count at the session rate.
    @Test func workerOverflowBecomesDroppedSeconds() {
        #expect(LiveCaptureNotice.classify(kind: "overflow", detail: "capture queue full; dropped_frames=24000") == .droppedAudio(seconds: 1.5))
        #expect(LiveCaptureNotice.classify(kind: "overflow", detail: "queue full; dropped_frames=1600") == .droppedAudio(seconds: 0.1))
    }

    /// The client's own overflow notice is already short and localized; problems
    /// that need the user stay as the worker words them.
    @Test func otherEventsShowTheirDetail() {
        #expect(LiveCaptureNotice.classify(kind: "overflow", detail: "Worker не успевает обрабатывать звук; фрагмент пропущен.") == .detail)
        #expect(LiveCaptureNotice.classify(kind: "overflow", detail: "dropped_frames=") == .detail)
        #expect(LiveCaptureNotice.classify(kind: "permission_denied", detail: "Microphone access denied") == .detail)
        #expect(LiveCaptureNotice.classify(kind: "device_removed", detail: "USB microphone removed") == .detail)
    }
}
