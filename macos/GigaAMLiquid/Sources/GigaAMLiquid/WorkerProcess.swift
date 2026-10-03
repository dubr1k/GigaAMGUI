import Darwin
import Foundation
import GigaAMLiquidCore

/// Error type shared by every worker-backed job.
struct WorkerFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
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
        let describe: (LineReader.Failure) -> Void = { onError(Self.describe($0)) }
        do {
            stdout = try LineReader(handle: stdoutPipe.fileHandleForReading, queue: queue, limit: Self.eventLimit,
                                    truncate: false, onLine: onLine, onError: describe,
                                    onEnd: { [weak self] in
                                        guard let self, self.process?.isRunning == true else { return }
                                        onStdoutEnd()
                                    })
            stderr = try LineReader(handle: stderrPipe.fileHandleForReading, queue: queue, limit: 16 * 1024,
                                    truncate: true, onLine: { onStderr(String(decoding: $0, as: UTF8.self)) },
                                    onError: describe)
        } catch let failure as LineReader.Failure {
            throw WorkerFailure(Self.describe(failure))
        }
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

    /// Pulls pending stderr (tracebacks) before a failure report is composed.
    /// Only stderr: this is called while a stdout line is being handled, and the
    /// stdout reader must not be re-entered from its own callback.
    func drainDiagnostics() {
        stderr?.drain()
    }

    /// One stdout event may carry a whole file result; the cap only stops a runaway line.
    static let eventLimit = 8 * 1024 * 1024

    static func describe(_ failure: LineReader.Failure) -> String {
        switch failure {
        case .configure(let code):
            return "Could not configure the transcription output pipe: \(String(cString: strerror(code)))"
        case .oversizedLine(let limit):
            // The event is gone, so the job cannot know its outcome; what the worker
            // already wrote is safe, and that is where the user should look.
            return "A worker event larger than \(limit / (1024 * 1024)) MiB was dropped, so the outcome of this job is unknown. Files the worker already saved remain on disk."
        case .readFailed(let code):
            return "Could not read transcription worker output: \(String(cString: strerror(code)))"
        }
    }

    func close() {
        closeInput()
        stdout?.close()
        stderr?.close()
        stdout = nil
        stderr = nil
    }
}
