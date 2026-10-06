import Foundation

/// The processing log shown in the log sheet: append-only lines with a size cap.
/// Past `limit` UTF-8 bytes it keeps the newest `keep` bytes, cut at a line start.
public struct LogBuffer {
    public private(set) var text = ""
    public let limit: Int
    public let keep: Int

    public init(limit: Int = 131_072, keep: Int = 65_536) {
        precondition(keep <= limit)
        self.limit = limit
        self.keep = keep
    }

    public var isEmpty: Bool { text.isEmpty }

    public mutating func append(_ line: String) {
        text += line.hasSuffix("\n") ? line : line + "\n"
        guard text.utf8.count > limit else { return }
        // A byte cut can split a character; the partial first line goes anyway.
        var tail = Substring(String(decoding: text.utf8.suffix(keep), as: UTF8.self))
        if let newline = tail.firstIndex(of: "\n") { tail = tail[tail.index(after: newline)...] }
        text = String(tail)
    }

    public mutating func clear() { text = "" }
}

/// Admission for a bounded queue fed from an audio thread: accepts up to `limit`
/// items in flight and reports overflow once per burst instead of per item.
public final class BacklogGate {
    public enum Decision: Equatable {
        /// Queue the item; `endedBurst` items were dropped since the last accepted one.
        case accept(endedBurst: Int)
        /// First item dropped in a burst: report the overflow now.
        case dropFirst
        /// Further items dropped in the same burst: count them silently.
        case drop
    }

    private let lock = NSLock()
    private let limit: Int
    private var inFlight = 0
    private var dropped = 0

    public init(limit: Int) { self.limit = limit }

    public func admit() -> Decision {
        lock.lock()
        defer { lock.unlock() }
        guard inFlight < limit else {
            dropped += 1
            return dropped == 1 ? .dropFirst : .drop
        }
        inFlight += 1
        let ended = dropped
        dropped = 0
        return .accept(endedBurst: ended)
    }

    /// An accepted item has been handled.
    public func release() {
        lock.lock()
        inFlight = max(0, inFlight - 1)
        lock.unlock()
    }
}
