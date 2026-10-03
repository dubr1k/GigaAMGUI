import Darwin
import Foundation

/// Nonblocking reader for one worker pipe: drains it on `queue` without growing an
/// unbounded callback queue or waiting forever for a descendant's open pipe, and
/// hands out whole lines (see `JSONLineFramer`).
///
/// Every callback runs on `queue`. Lines are cut out of the read buffer first and
/// dispatched afterwards, and a `drain()` issued from inside a callback is a no-op
/// (the running drain keeps reading), so a consumer can never re-enter the reader
/// and interleave or garble lines.
public final class LineReader {
    public enum Failure: Error, Equatable {
        case configure(Int32)
        /// A line exceeded `limit` bytes and was dropped (only when `truncate` is off).
        case oversizedLine(limit: Int)
        case readFailed(Int32)
    }

    private let handle: FileHandle
    private let onLine: (Data) -> Void
    private let onError: (Failure) -> Void
    private let onEnd: () -> Void
    private var source: DispatchSourceRead?
    private var buffer = [UInt8](repeating: 0, count: 8192)
    private var framer: JSONLineFramer
    private var draining = false

    public init(handle: FileHandle, queue: DispatchQueue, limit: Int, truncate: Bool,
                onLine: @escaping (Data) -> Void, onError: @escaping (Failure) -> Void,
                onEnd: @escaping () -> Void = {}) throws {
        self.handle = handle
        self.framer = JSONLineFramer(limit: limit, truncate: truncate)
        self.onLine = onLine
        self.onError = onError
        self.onEnd = onEnd
        let flags = fcntl(handle.fileDescriptor, F_GETFL)
        guard flags != -1, fcntl(handle.fileDescriptor, F_SETFL, flags | O_NONBLOCK) != -1 else {
            throw Failure.configure(errno)
        }
        let source = DispatchSource.makeReadSource(fileDescriptor: handle.fileDescriptor, queue: queue)
        self.source = source
        source.setEventHandler { [weak self] in self?.drain() }
        source.setCancelHandler { try? handle.close() }
        source.resume()
    }

    public var isOpen: Bool { source != nil }

    /// Reads what is available now. Must be called on `queue`.
    public func drain() {
        // Re-entry from a callback: the drain below this frame is still reading.
        guard source != nil, !draining else { return }
        draining = true
        defer { draining = false }
        // Bound one turn so busy diagnostics cannot starve Cancel/teardown.
        for _ in 0..<64 {
            let count = buffer.withUnsafeMutableBytes { Darwin.read(handle.fileDescriptor, $0.baseAddress, $0.count) }
            if count > 0 {
                let frames = framer.feed(buffer[0..<count])
                guard dispatch(frames) else { return }
            } else if count == 0 {
                _ = dispatch(framer.finish())
                close()
                onEnd()
                return
            } else if errno == EINTR {
                continue
            } else if errno == EAGAIN || errno == EWOULDBLOCK {
                return
            } else {
                let code = errno
                close()
                onError(.readFailed(code))
                return
            }
        }
    }

    /// False once a callback closed the reader: the rest is not wanted any more.
    private func dispatch(_ frames: [JSONLineFramer.Frame]) -> Bool {
        for frame in frames {
            guard source != nil else { return false }
            switch frame {
            case .line(let line): onLine(line)
            case .oversized: onError(.oversizedLine(limit: framer.limit))
            }
        }
        return source != nil
    }

    public func close() {
        source?.cancel()
        source = nil
    }

    deinit { close() }
}
