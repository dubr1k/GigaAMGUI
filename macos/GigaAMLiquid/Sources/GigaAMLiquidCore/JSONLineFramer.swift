import Foundation

/// Splits a byte stream into newline-terminated lines with a per-line size cap.
///
/// Pure and synchronous: `feed` returns every frame the bytes completed, and the
/// framer's own state is already consistent when the caller dispatches them. A
/// callback that reads more of the stream (or feeds this framer again) can no
/// longer corrupt a line that is still being cut out of a shared read buffer —
/// the way the batch worker's stdout reader garbled a `file_completed` line and
/// aborted the batch with "malformed JSON".
public struct JSONLineFramer {
    public enum Frame: Equatable {
        case line(Data)
        /// A line grew past `limit` (and `truncate` is off): it is dropped up to
        /// its newline, and the stream resumes with the next line.
        case oversized
    }

    public let limit: Int
    /// Deliver the first `limit` bytes of an oversized line instead of `.oversized`
    /// (stderr diagnostics: a long traceback line is still worth showing).
    public let truncate: Bool
    private var line = Data()
    private var dropping = false

    public init(limit: Int, truncate: Bool) {
        precondition(limit > 0, "limit must be positive")
        self.limit = limit
        self.truncate = truncate
    }

    public mutating func feed<Bytes: Collection>(_ bytes: Bytes) -> [Frame] where Bytes.Element == UInt8 {
        var frames: [Frame] = []
        var start = bytes.startIndex
        while start != bytes.endIndex {
            let newline = bytes[start...].firstIndex(of: 10)
            let end = newline ?? bytes.endIndex
            if !dropping {
                let available = max(0, limit - line.count)
                let length = bytes.distance(from: start, to: end)
                line.append(contentsOf: bytes[start..<bytes.index(start, offsetBy: min(length, available))])
                if length > available {
                    dropping = true
                    frames.append(truncate ? .line(line) : .oversized)
                    line.removeAll(keepingCapacity: true)
                }
            }
            guard let newline else { break }
            if !dropping { frames.append(.line(line)) }
            line.removeAll(keepingCapacity: true)
            dropping = false
            start = bytes.index(after: newline)
        }
        return frames
    }

    /// End of stream: a final line without a trailing newline is still a line.
    public mutating func finish() -> [Frame] {
        defer {
            line.removeAll(keepingCapacity: false)
            dropping = false
        }
        return line.isEmpty || dropping ? [] : [.line(line)]
    }
}
