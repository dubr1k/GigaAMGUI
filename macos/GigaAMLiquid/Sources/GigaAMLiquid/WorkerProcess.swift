import Darwin
import Foundation

/// Error type shared by every worker-backed job.
struct WorkerFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

/// Redacts secrets and credential-looking substrings from worker output before it reaches logs or the UI.
enum WorkerRedaction {
    private static let credentialPatterns = [
        #"\b(?:hf_|sk-)[A-Za-z0-9_-]+"#,
        #"(?i)\bBearer\s+\S+"#,
        #"(?i)((?:token|api[_-]?key|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+"#,
        #"(?i)(https?://)[^\s/@]+:[^\s/@]+@"#
    ].compactMap { try? NSRegularExpression(pattern: $0) }

    static func safeText(_ text: String, secrets: [String]) -> String {
        var value = text
        for secret in secrets { value = value.replacingOccurrences(of: secret, with: "[redacted]") }
        for pattern in credentialPatterns {
            value = pattern.stringByReplacingMatches(in: value, range: NSRange(value.startIndex..., in: value), withTemplate: "[redacted]")
        }
        return String(value.suffix(8192))
    }

    /// Environment values that look like credentials, for `safeText(_:secrets:)`.
    static func secrets(in environment: [String: String]) -> [String] {
        environment.compactMap { key, value in
            let name = key.uppercased()
            return value.count >= 6 && ["TOKEN", "SECRET", "PASSWORD", "API_KEY"].contains(where: name.contains) ? value : nil
        }
    }
}

/// One JSONL worker process: stdin for commands, stdout for events, stderr for diagnostics.
///
/// Every callback runs on `queue`. The owner decides what a line means; this
/// class only guarantees pipe hygiene (non-blocking reads, no SIGPIPE, bounded
/// lines) and a graceful → forced termination ladder.
final class WorkerProcess {
    private let queue: DispatchQueue
    private var process: Process?
    private var input: FileHandle?
    private var stdout: LineReader?
    private var stderr: LineReader?

    init(runtime: PythonRuntime, arguments: [String], environment: [String: String], queue: DispatchQueue,
         onLine: @escaping (Data) -> Void, onStderr: @escaping (String) -> Void,
         onStdoutEnd: @escaping () -> Void, onError: @escaping (String) -> Void,
         onExit: @escaping (Int32) -> Void) throws {
        self.queue = queue
        let task = Process()
        task.executableURL = runtime.executable
        task.arguments = arguments
        task.currentDirectoryURL = runtime.workingDirectory
        task.environment = environment
        let stdinPipe = Pipe(), stdoutPipe = Pipe(), stderrPipe = Pipe()
        task.standardInput = stdinPipe
        task.standardOutput = stdoutPipe
        task.standardError = stderrPipe
        // A worker exiting between isRunning and write must not SIGPIPE the app.
        guard fcntl(stdinPipe.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1) != -1 else {
            throw WorkerFailure("Could not configure the worker input pipe: \(String(cString: strerror(errno)))")
        }
        input = stdinPipe.fileHandleForWriting
        stdout = try LineReader(handle: stdoutPipe.fileHandleForReading, queue: queue, limit: 8 * 1024 * 1024,
                                truncate: false, onLine: onLine, onError: onError,
                                onEnd: { [weak self] in
                                    guard let self, self.process?.isRunning == true else { return }
                                    onStdoutEnd()
                                })
        stderr = try LineReader(handle: stderrPipe.fileHandleForReading, queue: queue, limit: 16 * 1024,
                                truncate: true, onLine: { onStderr(String(decoding: $0, as: UTF8.self)) },
                                onError: onError)
        // Retain the worker until the OS has reaped it, even if its UI is rebuilt.
        task.terminationHandler = { [self] task in
            queue.async {
                // Exit notification can win the race with a read-source callback. Drain
                // the remaining bytes first, without waiting on inherited child pipe FDs.
                self.stdout?.drain()
                self.stderr?.drain()
                task.terminationHandler = nil
                self.process = nil
                onExit(task.terminationStatus)
            }
        }
        do {
            try task.run()
            process = task
        } catch {
            task.terminationHandler = nil
            throw WorkerFailure("Could not launch the project Python (\(runtime.executable.path)): \(error.localizedDescription)")
        }
        try? stdinPipe.fileHandleForReading.close()
        try? stdoutPipe.fileHandleForWriting.close()
        try? stderrPipe.fileHandleForWriting.close()
    }

    var isRunning: Bool { process?.isRunning == true }

    func send(_ command: [String: Any]) throws {
        guard let input, let process, process.isRunning else { throw WorkerFailure("The transcription worker is not running.") }
        var data = try JSONSerialization.data(withJSONObject: command)
        data.append(10)
        try input.write(contentsOf: data)
    }

    /// EOF on stdin is the worker's normal shutdown signal (main() loops over stdin).
    func closeInput() {
        try? input?.close()
        input = nil
    }

    /// SIGTERM after `delay`, SIGKILL `delay` later, unless the worker exits by itself.
    func terminateGracefully(after delay: TimeInterval) {
        guard let task = process else { return }
        queue.asyncAfter(deadline: .now() + delay) { [weak self, weak task] in
            guard let self, let task, self.process === task, task.isRunning else { return }
            task.terminate()
            self.queue.asyncAfter(deadline: .now() + delay) { [weak self, weak task] in
                guard let self, let task, self.process === task, task.isRunning else { return }
                Darwin.kill(task.processIdentifier, SIGKILL)
            }
        }
    }

    /// Immediate SIGKILL; used when the application is about to exit.
    func kill() {
        if let task = process, task.isRunning { Darwin.kill(task.processIdentifier, SIGKILL) }
    }

    func drain() {
        stdout?.drain()
        stderr?.drain()
    }

    func close() {
        closeInput()
        stdout?.close()
        stderr?.close()
        stdout = nil
        stderr = nil
    }
}

/// Nonblocking readers drain both pipes concurrently without growing an
/// unbounded callback queue or waiting forever for a descendant's open pipe.
final class LineReader {
    private let handle: FileHandle
    private let limit: Int
    private let truncate: Bool
    private let onLine: (Data) -> Void
    private let onError: (String) -> Void
    private let onEnd: () -> Void
    private var source: DispatchSourceRead?
    private var buffer = [UInt8](repeating: 0, count: 8192)
    private var line = Data()
    private var dropping = false

    init(handle: FileHandle, queue: DispatchQueue, limit: Int, truncate: Bool,
         onLine: @escaping (Data) -> Void, onError: @escaping (String) -> Void,
         onEnd: @escaping () -> Void = {}) throws {
        self.handle = handle
        self.limit = limit
        self.truncate = truncate
        self.onLine = onLine
        self.onError = onError
        self.onEnd = onEnd
        let flags = fcntl(handle.fileDescriptor, F_GETFL)
        guard flags != -1, fcntl(handle.fileDescriptor, F_SETFL, flags | O_NONBLOCK) != -1 else {
            throw WorkerFailure("Could not configure the transcription output pipe.")
        }
        let source = DispatchSource.makeReadSource(fileDescriptor: handle.fileDescriptor, queue: queue)
        self.source = source
        source.setEventHandler { [weak self] in self?.drain() }
        source.setCancelHandler { try? handle.close() }
        source.resume()
    }

    func drain() {
        guard source != nil else { return }
        // Bound one turn so busy diagnostics cannot starve Cancel/teardown.
        for _ in 0..<64 {
            let count = buffer.withUnsafeMutableBytes { Darwin.read(handle.fileDescriptor, $0.baseAddress, $0.count) }
            if count > 0 {
                consume(count)
            } else if count == 0 {
                if !line.isEmpty && !dropping { onLine(line) }
                line.removeAll(keepingCapacity: false)
                close()
                onEnd()
                return
            } else if errno == EINTR {
                continue
            } else if errno == EAGAIN || errno == EWOULDBLOCK {
                return
            } else {
                onError("Could not read transcription worker output: \(String(cString: strerror(errno)))")
                close()
                return
            }
        }
    }

    private func consume(_ count: Int) {
        var start = 0
        while start < count {
            let newline = buffer[start..<count].firstIndex(of: 10)
            let end = newline ?? count
            if !dropping {
                let available = max(0, limit - line.count)
                line.append(contentsOf: buffer[start..<min(end, start + available)])
                if end - start > available {
                    dropping = true
                    if truncate { onLine(line) }
                    else { onError("The transcription worker exceeded the 8 MiB JSONL event limit.") }
                    line.removeAll(keepingCapacity: true)
                }
            }
            if let newline {
                if !dropping { onLine(line) }
                line.removeAll(keepingCapacity: true)
                dropping = false
                start = newline + 1
            } else { break }
        }
    }

    func close() {
        source?.cancel()
        source = nil
    }

    deinit { close() }
}
