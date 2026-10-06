import Foundation
import Testing
@testable import GigaAMLiquidCore

@Suite struct JSONLineFramerTests {
    private func lines(_ frames: [JSONLineFramer.Frame]) -> [String] {
        frames.map { frame in
            switch frame {
            case .line(let data): return String(decoding: data, as: UTF8.self)
            case .oversized: return "<oversized>"
            }
        }
    }

    @Test func linesSplitAcrossChunksAreJoined() {
        var framer = JSONLineFramer(limit: 1024, truncate: false)
        #expect(lines(framer.feed(Array(#"{"type":"lo"#.utf8))).isEmpty)
        #expect(lines(framer.feed(Array("g\"}\n{\"type\":".utf8))) == [#"{"type":"log"}"#])
        #expect(lines(framer.feed(Array("\"x\"}\n\n".utf8))) == [#"{"type":"x"}"#, ""])
        #expect(lines(framer.finish()).isEmpty)
    }

    @Test func trailingLineWithoutNewlineIsDeliveredAtEnd() {
        var framer = JSONLineFramer(limit: 1024, truncate: false)
        #expect(lines(framer.feed(Array("a\nb".utf8))) == ["a"])
        #expect(lines(framer.finish()) == ["b"])
        #expect(lines(framer.finish()).isEmpty)
    }

    @Test func oversizedLineIsReportedOnceAndTheStreamResumes() {
        var framer = JSONLineFramer(limit: 4, truncate: false)
        #expect(lines(framer.feed(Array("123".utf8))).isEmpty)
        #expect(lines(framer.feed(Array("456".utf8))) == ["<oversized>"])
        #expect(lines(framer.feed(Array("789\nok\n".utf8))) == ["ok"])
        // A line of exactly `limit` bytes still fits.
        #expect(lines(framer.feed(Array("1234\n".utf8))) == ["1234"])
        #expect(lines(framer.feed(Array("12345".utf8))) == ["<oversized>"])
        #expect(lines(framer.finish()).isEmpty)
    }

    @Test func truncatingFramerKeepsTheHeadOfALongLine() {
        var framer = JSONLineFramer(limit: 4, truncate: true)
        #expect(lines(framer.feed(Array("abcdefgh\nij\n".utf8))) == ["abcd", "ij"])
    }
}

@Suite struct LineReaderTests {
    /// Writes `payload` into a pipe and reads it back through a LineReader whose
    /// line callback re-enters `drain()` — what NativeTranscriptionJob did for every
    /// failed file. The re-entered drain used to overwrite the shared read buffer
    /// while the outer one was still cutting lines out of it.
    private func readBack(_ payload: Data, limit: Int = 1 << 20) throws -> (lines: [String], failures: [LineReader.Failure]) {
        let pipe = Pipe()
        let queue = DispatchQueue(label: "LineReaderTests")
        let done = DispatchSemaphore(value: 0)
        var lines: [String] = []
        var failures: [LineReader.Failure] = []
        var reader: LineReader?
        try queue.sync {
            reader = try LineReader(
                handle: pipe.fileHandleForReading, queue: queue, limit: limit, truncate: false,
                onLine: { line in
                    lines.append(String(decoding: line, as: UTF8.self))
                    reader?.drain()
                },
                onError: { failures.append($0) },
                onEnd: { done.signal() }
            )
        }
        DispatchQueue.global().async {
            pipe.fileHandleForWriting.write(payload)
            try? pipe.fileHandleForWriting.close()
        }
        #expect(done.wait(timeout: .now() + 10) == .success)
        return queue.sync { (lines, failures) }
    }

    @Test func reenteredDrainDoesNotGarbleOrReorderLines() throws {
        // Lines longer than the 8 KiB read buffer, so one read ends mid-line and the
        // next read is the one a re-entered drain would have stolen.
        let expected = (0..<40).map { index in
            #"{"type":"file_completed","file_index":\#(index),"pad":""# + String(repeating: "x", count: 3000 + index * 97) + #""}"#
        }
        let result = try readBack(Data((expected.joined(separator: "\n") + "\n").utf8))
        #expect(result.failures.isEmpty)
        #expect(result.lines == expected)
        for line in result.lines {
            #expect((try? JSONSerialization.jsonObject(with: Data(line.utf8))) != nil)
        }
    }

    @Test func oversizedLineIsReportedAndFollowingLinesSurvive() throws {
        let payload = "first\n" + String(repeating: "y", count: 20_000) + "\nlast"
        let result = try readBack(Data(payload.utf8), limit: 10_000)
        #expect(result.lines == ["first", "last"])
        #expect(result.failures == [.oversizedLine(limit: 10_000)])
    }
}
