import Foundation
import CoreFoundation
import Darwin

struct NativeTranscriptionSettings {
    var formats: [String] = ["txt"]
    var backend = "auto"
    var model = "v3_e2e_rnnt"
    var onnxProvider = "auto"
    var diarization = false
    var diarizationBackend = "pyannote"
    var numSpeakers: Int? = nil
    var audioPreprocessingMode = "auto"
    var subtitleSentenceSplit = true
    var subtitleMaxLines = 2
    var subtitleMaxWidth = 64
    var hfToken: String? = nil
}

struct NativeTranscriptionResult {
    let inputURL: URL
    let transcript: String
    let outputFiles: [String: URL]
    let error: String?
    let metadataJSON: String
}

enum NativeTranscriptionEvent {
    case log(String)
    /// The file index is zero-based, matching src.tui_worker.
    case fileStarted(URL, Int, Int)
    case progress(Double?, String)
    case fileCompleted(NativeTranscriptionResult)
    case completed(success: Bool, cancelled: Bool)
    case failed(String)
}

/// One worker per batch. Mutable state and pipe decoding belong to `queue`;
/// callbacks are ordered on the main queue, with exactly one terminal event.
final class NativeTranscriptionJob {
    private let files: [URL]
    private let outputDirectory: URL
    private let settings: NativeTranscriptionSettings
    private let onEvent: (NativeTranscriptionEvent) -> Void
    private let queue = DispatchQueue(label: "GigaAMLiquid.transcription", qos: .userInitiated)
    private var process: Process?
    private var input: FileHandle?
    private var stdout: LineReader?
    private var stderr: LineReader?
    private var started = false
    private var cancellationRequested = false
    private var pendingTerminal: NativeTranscriptionEvent?
    private var finished = false
    private var runtimeRoot: URL?
    private var resolvedOutputDirectory: URL?
    private var completedIndices = Set<Int>()
    private var hadFileError = false
    private var diagnostics = ""
    private var secrets: [String] = []

    init(files: [URL], outputDirectory: URL, settings: NativeTranscriptionSettings,
         onEvent: @escaping (NativeTranscriptionEvent) -> Void) {
        self.files = files.map { $0.standardizedFileURL }
        self.outputDirectory = outputDirectory.standardizedFileURL
        self.settings = settings
        self.onEvent = onEvent
    }

    func start() {
        queue.async {
            guard !self.started, !self.finished else { return }
            self.started = true
            do { try self.launch() }
            catch { self.requestTerminal(.failed(self.safeText(error.localizedDescription))) }
        }
    }

    /// The shared processor cannot safely interrupt a file; do not kill it here.
    func cancel() {
        queue.async {
            guard !self.finished, self.pendingTerminal == nil, !self.cancellationRequested else { return }
            self.cancellationRequested = true
            guard self.started else {
                self.requestTerminal(.completed(success: false, cancelled: true))
                return
            }
            do { try self.send(["type": "cancel"]) }
            catch { self.requestTerminal(.failed(self.safeText(error.localizedDescription))) }
        }
    }

    /// Synchronous teardown: the application may exit immediately after returning.
    /// Unlike Cancel, this deliberately stops an in-flight file without waiting.
    func terminate() {
        queue.sync {
            guard !self.finished else { return }
            self.requestTerminal(.completed(success: false, cancelled: true))
            if let task = self.process, task.isRunning {
                Darwin.kill(task.processIdentifier, SIGKILL)
            }
        }
    }

    private func launch() throws {
        guard !files.isEmpty else { throw Failure("No input files supplied.") }
        let manager = FileManager.default
        for file in files {
            guard file.isFileURL,
                  (try? file.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true,
                  manager.isReadableFile(atPath: file.path) else {
                throw Failure("Input file is not readable: \(file.path)")
            }
        }
        guard outputDirectory.isFileURL else { throw Failure("Output directory must be a local folder.") }
        let runtime = try PythonRuntime.resolve()
        runtimeRoot = runtime.root
        guard manager.isReadableFile(atPath: runtime.root.appendingPathComponent("src/tui_worker.py").path) else {
            throw Failure("The Python project is missing src/tui_worker.py.")
        }
        try manager.createDirectory(at: outputDirectory, withIntermediateDirectories: true)
        resolvedOutputDirectory = outputDirectory.resolvingSymlinksInPath().standardizedFileURL
        var environment = runtime.environment
        if let token = settings.hfToken?.trimmingCharacters(in: .whitespacesAndNewlines), !token.isEmpty {
            environment["HF_TOKEN"] = token
        }
        secrets = environment.compactMap { key, value in
            let name = key.uppercased()
            return value.count >= 6 && ["TOKEN", "SECRET", "PASSWORD", "API_KEY"].contains(where: name.contains) ? value : nil
        }
        let command: [String: Any] = [
            "type": "start", "files": files.map(\.path), "output_dir": outputDirectory.path,
            "formats": settings.formats, "backend": settings.backend, "model": settings.model,
            "onnx_provider": settings.onnxProvider, "diarization": settings.diarization,
            "diarization_backend": settings.diarizationBackend,
            "num_speakers": settings.numSpeakers.map { $0 as Any } ?? NSNull(),
            "audio_preprocessing_mode": settings.audioPreprocessingMode,
            "subtitle_sentence_split": settings.subtitleSentenceSplit,
            "subtitle_max_lines": settings.subtitleMaxLines, "subtitle_max_width": settings.subtitleMaxWidth
        ]
        let task = Process()
        task.executableURL = runtime.executable
        task.arguments = ["-m", "src.tui_worker"]
        task.currentDirectoryURL = runtime.root
        task.environment = environment
        let stdinPipe = Pipe(), stdoutPipe = Pipe(), stderrPipe = Pipe()
        task.standardInput = stdinPipe
        task.standardOutput = stdoutPipe
        task.standardError = stderrPipe
        // A worker exiting between isRunning and write must not SIGPIPE the app.
        guard fcntl(stdinPipe.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1) != -1 else {
            throw Failure("Could not configure the worker input pipe: \(String(cString: strerror(errno)))")
        }
        input = stdinPipe.fileHandleForWriting
        stdout = try LineReader(handle: stdoutPipe.fileHandleForReading, queue: queue, limit: 8 * 1024 * 1024,
                                truncate: false, onLine: { [weak self] in self?.consume($0) },
                                onError: { [weak self] in self?.requestTerminal(.failed($0)) },
                                onEnd: { [weak self] in
                                    guard let self, self.process?.isRunning == true else { return }
                                    self.requestTerminal(.failed(self.failureDetails("The transcription worker closed stdout without a completion event.")))
                                })
        stderr = try LineReader(handle: stderrPipe.fileHandleForReading, queue: queue, limit: 16 * 1024,
                                truncate: true, onLine: { [weak self] in self?.recordLog(String(decoding: $0, as: UTF8.self)) },
                                onError: { [weak self] in self?.requestTerminal(.failed($0)) })
        // Retain the job until the OS has reaped the worker, even if its UI is rebuilt.
        task.terminationHandler = { _ in self.queue.async { self.workerExited() } }
        do {
            try task.run()
            process = task
        } catch {
            task.terminationHandler = nil
            throw Failure("Could not launch the project Python (\(runtime.executable.path)): \(error.localizedDescription)")
        }
        try? stdinPipe.fileHandleForReading.close()
        try? stdoutPipe.fileHandleForWriting.close()
        try? stderrPipe.fileHandleForWriting.close()
        try send(command)
        emit(.progress(nil, "Loading GigaAM model…"))
    }

    private func send(_ command: [String: Any]) throws {
        guard let input, let process, process.isRunning else { throw Failure("The transcription worker is not running.") }
        var data = try JSONSerialization.data(withJSONObject: command)
        data.append(10)
        try input.write(contentsOf: data)
    }

    private func consume(_ line: Data) {
        guard !finished, pendingTerminal == nil, !line.isEmpty else { return }
        let object: Any
        do { object = try JSONSerialization.jsonObject(with: line) }
        catch {
            // Some Python dependencies print ordinary messages to stdout.
            let text = String(decoding: line, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            if text.hasPrefix("{") {
                requestTerminal(.failed("The transcription worker returned malformed JSON."))
            } else {
                recordLog(text)
            }
            return
        }
        guard let message = object as? [String: Any], let type = message["type"] as? String else {
            requestTerminal(.failed("The transcription worker returned an invalid event."))
            return
        }
        do {
            switch type {
            case "started":
                guard integer(message["total_files"]) == files.count else { throw Failure("Invalid worker batch size.") }
            case "log", "cancelling":
                guard let text = message["message"] as? String else { throw Failure("Invalid worker log event.") }
                recordLog(text)
            case "file_started":
                let index = try fileIndex(message)
                emit(.fileStarted(files[index], index, files.count))
                emit(.progress(Double(index) / Double(files.count), files[index].lastPathComponent))
            case "progress":
                let index = try fileIndex(message)
                guard let stage = message["stage"] as? String else { throw Failure("Invalid worker progress stage.") }
                let text = safeText(message["message"] as? String ?? stage)
                let fraction: Double?
                if message["stage_progress"] is NSNull || message["file_progress"] is NSNull {
                    fraction = nil
                } else if let value = message["file_progress"] as? NSNumber,
                          CFGetTypeID(value) != CFBooleanGetTypeID(), value.doubleValue.isFinite {
                    fraction = (Double(index) + min(1, max(0, value.doubleValue))) / Double(files.count)
                } else { throw Failure("Invalid worker progress value.") }
                emit(.progress(fraction, text))
            case "file_completed":
                let index = try fileIndex(message, requireTotal: false)
                guard let raw = message["result"] as? [String: Any] else { throw Failure("Invalid worker file result.") }
                try acceptResult(raw, index: index)
            case "completed":
                guard let success = boolean(message["success"]), let cancelled = boolean(message["cancelled"]),
                      let results = message["results"] as? [[String: Any]], results.count <= files.count else {
                    throw Failure("Invalid worker completion event.")
                }
                for (index, raw) in results.enumerated() { try acceptResult(raw, index: index) }
                if let text = message["message"] as? String { recordLog(text) }
                if success && completedIndices.count != files.count { throw Failure("The worker completed without results for every file.") }
                if !success && !cancelled && completedIndices.isEmpty {
                    requestTerminal(.failed(failureDetails(message["message"] as? String ?? "Transcription failed before processing any files.")))
                } else {
                    requestTerminal(.completed(success: success && !hadFileError, cancelled: cancelled))
                }
            case "error":
                guard let text = message["message"] as? String else { throw Failure("Invalid worker error event.") }
                recordLog(text)
                if let traceback = message["traceback"] as? String { recordLog(traceback) }
                requestTerminal(.failed(safeText(text)))
            default:
                throw Failure("Unexpected transcription worker event: \(safeText(type))")
            }
        } catch { requestTerminal(.failed(safeText(error.localizedDescription))) }
    }

    private func fileIndex(_ message: [String: Any], requireTotal: Bool = true) throws -> Int {
        guard let index = integer(message["file_index"]), files.indices.contains(index),
              let path = message["file"] as? String,
              URL(fileURLWithPath: path).standardizedFileURL == files[index],
              !requireTotal || integer(message["total_files"]) == files.count else {
            throw Failure("The transcription worker returned an invalid file reference.")
        }
        return index
    }

    private func acceptResult(_ raw: [String: Any], index: Int) throws {
        guard files.indices.contains(index), let success = boolean(raw["success"]),
              let path = raw["file_path"] as? String,
              URL(fileURLWithPath: path).standardizedFileURL == files[index],
              let saved = raw["saved_files"] as? [String] else { throw Failure("The transcription worker returned an invalid result payload.") }
        // `completed` repeats the same result objects; do not re-read files or re-emit them.
        guard !completedIndices.contains(index) else { return }
        if !success { stderr?.drain() }
        let metadata = try JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys])
        let stem = files[index].deletingPathExtension().lastPathComponent
        let suffixes = ["txt": ".txt", "txt_timecodes": "_timecodes.txt", "txt_diarize": "_diarize.txt",
                        "txt_diarize_timecodes": "_diarize_timecodes.txt", "md": ".md", "srt": ".srt", "vtt": ".vtt"]
        var outputFiles: [String: URL] = [:]
        var errors: [String] = []
        if let error = raw["error"] as? String, !error.isEmpty { errors.append(error) }
        if let diarization = raw["diarization"] as? [String: Any],
           let error = diarization["error"] as? String, !error.isEmpty { errors.append(error) }
        for savedPath in saved {
            guard let root = runtimeRoot, let directory = resolvedOutputDirectory else { throw Failure("Missing Python runtime context.") }
            let reported = URL(fileURLWithPath: savedPath, relativeTo: root).standardizedFileURL
            let file = reported.resolvingSymlinksInPath().standardizedFileURL
            let expectedName = reported.lastPathComponent
            guard !savedPath.contains("\0"), file.deletingLastPathComponent().path == directory.path,
                  let format = suffixes.first(where: { stem + $0.value == expectedName })?.key,
                  let values = try? file.resourceValues(forKeys: [.isRegularFileKey]), values.isRegularFile == true,
                  FileManager.default.isReadableFile(atPath: file.path) else {
                errors.append("The worker returned an unavailable or unsafe output file: \(savedPath)")
                continue
            }
            outputFiles[format] = file
        }
        var transcript = ""
        // The processor returns paths, not text. Never synthesize a transcript from logs.
        for format in ["txt", "txt_diarize", "txt_timecodes", "txt_diarize_timecodes", "md"] {
            guard let file = outputFiles[format] else { continue }
            do {
                let handle = try FileHandle(forReadingFrom: file)
                defer { try? handle.close() }
                let limit = 32 * 1024 * 1024
                let data = try handle.read(upToCount: limit + 1) ?? Data()
                guard data.count <= limit else { throw Failure("Transcript is larger than the 32 MiB preview limit; open the saved file.") }
                guard let text = String(data: data, encoding: .utf8) else { throw Failure("The saved transcript is not valid UTF-8.") }
                transcript = text
                break
            } catch { errors.append(error.localizedDescription) }
        }
        if !success && errors.isEmpty { errors.append(failureDetails("The Python processor could not transcribe this file.")) }
        if success && outputFiles.isEmpty { errors.append("The Python processor did not produce any readable output files.") }
        let error = errors.isEmpty ? nil : safeText(errors.joined(separator: "\n"))
        hadFileError = hadFileError || !success || error != nil
        completedIndices.insert(index)
        emit(.fileCompleted(NativeTranscriptionResult(inputURL: files[index], transcript: transcript,
                                                     outputFiles: outputFiles, error: error,
                                                     metadataJSON: String(decoding: metadata, as: UTF8.self))))
    }

    private func boolean(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }

    private func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite, number.doubleValue >= 0,
              number.doubleValue < Double(Int.max), number.doubleValue.rounded(.down) == number.doubleValue else { return nil }
        return number.intValue
    }

    private func emit(_ event: NativeTranscriptionEvent) {
        guard !finished, pendingTerminal == nil else { return }
        DispatchQueue.main.async { self.onEvent(event) }
    }

    private func recordLog(_ text: String) {
        guard !finished, pendingTerminal == nil else { return }
        let text = safeText(text).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        diagnostics = String((diagnostics + "\n" + text).suffix(16 * 1024))
        emit(.log(text))
    }

    private static let credentialPatterns = [
        #"\b(?:hf_|sk-)[A-Za-z0-9_-]+"#,
        #"(?i)\bBearer\s+\S+"#,
        #"(?i)((?:token|api[_-]?key|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+"#,
        #"(?i)(https?://)[^\s/@]+:[^\s/@]+@"#
    ].compactMap { try? NSRegularExpression(pattern: $0) }

    private func safeText(_ text: String) -> String {
        var value = text
        for secret in secrets { value = value.replacingOccurrences(of: secret, with: "[redacted]") }
        for pattern in Self.credentialPatterns {
            value = pattern.stringByReplacingMatches(in: value, range: NSRange(value.startIndex..., in: value), withTemplate: "[redacted]")
        }
        return String(value.suffix(8192))
    }

    private func failureDetails(_ message: String) -> String {
        let summary = safeText(message)
        return diagnostics.isEmpty ? summary : summary + "\n" + safeText(diagnostics)
    }

    private func requestTerminal(_ event: NativeTranscriptionEvent) {
        guard !finished, pendingTerminal == nil else { return }
        pendingTerminal = event
        // main() loops over stdin and its processing thread is a daemon. EOF is
        // the normal shutdown protocol, but only AFTER the batch has completed.
        try? input?.close()
        input = nil
        guard let task = process else { finish(); return }
        queue.asyncAfter(deadline: .now() + 2) { [weak self, weak task] in
            guard let self, let task, self.process === task, task.isRunning else { return }
            task.terminate()
            self.queue.asyncAfter(deadline: .now() + 2) { [weak self, weak task] in
                guard let self, let task, self.process === task, task.isRunning else { return }
                Darwin.kill(task.processIdentifier, SIGKILL)
            }
        }
    }

    private func workerExited() {
        guard !finished, let task = process else { return }
        // Exit notification can win the race with a read-source callback. Drain
        // the remaining bytes first, without waiting on inherited child pipe FDs.
        stdout?.drain()
        stderr?.drain()
        if pendingTerminal == nil {
            pendingTerminal = .failed(failureDetails("The transcription worker exited without a completion event (status \(task.terminationStatus))."))
        }
        task.terminationHandler = nil
        process = nil
        finish()
    }

    private func finish() {
        guard !finished, let event = pendingTerminal else { return }
        finished = true
        try? input?.close()
        input = nil
        stdout?.close()
        stderr?.close()
        stdout = nil
        stderr = nil
        DispatchQueue.main.async { self.onEvent(event) }
    }

    private struct Failure: LocalizedError {
        let message: String
        init(_ message: String) { self.message = message }
        var errorDescription: String? { message }
    }

    /// Nonblocking readers drain both pipes concurrently without growing an
    /// unbounded callback queue or waiting forever for a descendant's open pipe.
    private final class LineReader {
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
                throw Failure("Could not configure the transcription output pipe.")
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
}
