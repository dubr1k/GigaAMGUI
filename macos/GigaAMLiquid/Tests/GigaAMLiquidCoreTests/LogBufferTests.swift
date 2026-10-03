import Testing
@testable import GigaAMLiquidCore

@Suite struct LogBufferTests {
    @Test func linesEndWithNewlines() {
        var log = LogBuffer()
        log.append("один")
        log.append("два\n")
        #expect(log.text == "один\nдва\n")
    }

    /// Several writers appended to the log string directly and skipped the cap.
    @Test func capKeepsTheNewestWholeLines() {
        var log = LogBuffer(limit: 100, keep: 40)
        for index in 0..<20 { log.append("строка \(index)") }
        #expect(log.text.utf8.count <= 100)
        #expect(log.text.hasSuffix("строка 19\n"))
        #expect(log.text.hasPrefix("строка "))
    }
}

@Suite struct BacklogGateTests {
    /// A worker that falls behind dropped audio with one status update and one
    /// log line per 100 ms chunk; now a burst is reported once and summed up.
    @Test func overflowIsReportedOncePerBurst() {
        let gate = BacklogGate(limit: 2)
        #expect(gate.admit() == .accept(endedBurst: 0))
        #expect(gate.admit() == .accept(endedBurst: 0))
        #expect(gate.admit() == .dropFirst)
        #expect(gate.admit() == .drop)
        #expect(gate.admit() == .drop)
        gate.release()
        #expect(gate.admit() == .accept(endedBurst: 3))
        #expect(gate.admit() == .dropFirst)
    }
}
