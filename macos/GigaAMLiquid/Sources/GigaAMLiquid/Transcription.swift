import Foundation
import GigaAMLiquidCore

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
    /// Overall fraction for the bar, or nil to leave it as it is; plus a status line.
    case progress(Double?, String)
    case fileCompleted(NativeTranscriptionResult)
    case completed(success: Bool, cancelled: Bool)
    case failed(String)
}

/// One worker per batch. Mutable state and pipe decoding belong to `queue`;
/// callbacks are ordered on the main queue, with exactly one terminal event.
final class NativeTranscriptionJob {
    private let files: [URL]
    /// `nil` means "next to each input file", which src.tui_worker applies per file.
    private let outputDirectory: URL?
    private let settings: NativeTranscriptionSettings
    private let onEvent: (NativeTranscriptionEvent) -> Void
    private let queue = DispatchQueue(label: "GigaAMLiquid.transcription", qos: .userInitiated)
    private var worker: WorkerProcess?
    private var started = false
    private var cancellationRequested = false
    private var pendingTerminal: NativeTranscriptionEvent?
    private var finished = false
    private var runtimeRoot: URL?
    private var resolvedOutputDirectory: URL?
    private var completedIndices = Set<Int>()
    private var hadFileError = false
    /// Set once `hello` is sent; the first protocol event is its reply.
    private var awaitingHelloReply = false
    private var diagnostics = ""
    private var secrets: [String] = []

    private let resolveRuntime: PythonRuntime.Provider

    init(files: [URL], outputDirectory: URL?, settings: NativeTranscriptionSettings,
         runtime: @escaping PythonRuntime.Provider = PythonRuntime.resolveDefault,
         onEvent: @escaping (NativeTranscriptionEvent) -> Void) {
        self.files = files.map { $0.standardizedFileURL }
        self.outputDirectory = outputDirectory?.standardizedFileURL
        self.settings = settings
        self.resolveRuntime = runtime
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
            self.worker?.kill()
        }
    }

    private func launch() throws {
        guard !files.isEmpty else { throw WorkerFailure(L10n.text("Не выбраны файлы для обработки.")) }
        let manager = FileManager.default
        for file in files {
            guard file.isFileURL,
                  (try? file.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true,
                  manager.isReadableFile(atPath: file.path) else {
                throw WorkerFailure(L10n.format("Файл недоступен для чтения: %@", file.path))
            }
        }
        if let outputDirectory, !outputDirectory.isFileURL { throw WorkerFailure(L10n.text("Папка результатов должна быть локальной.")) }
        let runtime = try resolveRuntime()
        runtimeRoot = runtime.root
        // The frozen companion runs `--native-worker`; only the source-tree runtime needs src.tui_worker.
        guard runtime.frozenCompanion || manager.isReadableFile(atPath: runtime.root.appendingPathComponent("src/tui_worker.py").path) else {
            throw WorkerFailure(L10n.text("В Python-проекте нет src/tui_worker.py."))
        }
        if let outputDirectory {
            try manager.createDirectory(at: outputDirectory, withIntermediateDirectories: true)
            resolvedOutputDirectory = outputDirectory.resolvingSymlinksInPath().standardizedFileURL
        }
        var environment = runtime.environment
        if let token = settings.hfToken?.trimmingCharacters(in: .whitespacesAndNewlines), !token.isEmpty {
            environment["HF_TOKEN"] = token
        }
        secrets = WorkerRedaction.secrets(in: environment)
        let command: [String: Any] = [
            "type": "start", "files": files.map(\.path), "output_dir": outputDirectory?.path ?? "",
            "formats": settings.formats, "backend": settings.backend, "model": settings.model,
            "onnx_provider": settings.onnxProvider, "diarization": settings.diarization,
            "diarization_backend": settings.diarizationBackend,
            "num_speakers": settings.numSpeakers.map { $0 as Any } ?? NSNull(),
            "audio_preprocessing_mode": settings.audioPreprocessingMode,
            "subtitle_sentence_split": settings.subtitleSentenceSplit,
            "subtitle_max_lines": settings.subtitleMaxLines, "subtitle_max_width": settings.subtitleMaxWidth
        ]
        // The job retains itself through the worker callbacks until the OS has
        // reaped the process, even if its UI is rebuilt meanwhile.
        let worker = try WorkerProcess(
            role: .transcription, runtime: runtime, arguments: runtime.transcriptionArguments, environment: environment, queue: queue,
            onLine: { self.consume($0) },
            // Library warnings and tracebacks are for failure reports, not the user-facing log.
            onStderr: { self.recordDiagnostic($0) },
            onStdoutEnd: { self.requestTerminal(.failed(self.failureDetails(L10n.format("%@ закрыл вывод, не сообщив о завершении.", WorkerRole.transcription.name)))) },
            onError: { self.requestTerminal(.failed($0)) },
            onExit: { self.workerExited(status: $0) }
        )
        self.worker = worker
        // First the handshake: it asks the worker to keep `completed` to per-file
        // metadata (file_completed already carried each full result), so a long
        // batch's completion line stays far below the 8 MiB event cap. The batch
        // never waits for `ready`: an older worker answers with an error or not at all.
        try send(Self.hello)
        awaitingHelloReply = true
        try send(command)
        emit(.progress(nil, "Загружаем модель распознавания речи…"))
    }

    static let hello: [String: Any] = ["type": "hello", "client": "liquid", "features": ["compact_completed"]]

    private func send(_ command: [String: Any]) throws {
        guard let worker else { throw WorkerFailure(L10n.format("%@ не запущен.", WorkerRole.transcription.name)) }
        try worker.send(command)
    }

    private func consume(_ line: Data) {
        guard !finished, pendingTerminal == nil else { return }
        // The worker answers commands in order, so the first event replies to hello.
        let helloReply: Bool
        switch BatchEventDecoder.decode(line) {
        case nil:
            return
        case .text(let text):
            // Some Python dependencies print to stdout: plain text, a Python repr such as
            // "{'loaded': True}", a bare JSON value. That is log material, not a protocol
            // violation; the batch goes on and its results decide the outcome.
            recordLog(text)
            return
        case .unknown(let type):
            // The worker is shared with the TUI and gains events over time.
            awaitingHelloReply = false
            recordLog(L10n.format("Пропущено неизвестное событие воркера: %@", type))
            return
        case .invalid(let type):
            requestTerminal(.failed(safeText(L10n.format("%@ прислал событие %@ в неверном формате.", WorkerRole.transcription.name, type))))
            return
        case .event(let event):
            helloReply = awaitingHelloReply
            awaitingHelloReply = false
            do { try handle(event, helloReply: helloReply) }
            catch { requestTerminal(.failed(safeText(error.localizedDescription))) }
        }
    }

    private func handle(_ event: BatchEvent, helloReply: Bool) throws {
        switch event {
        case .ready:
            break  // compact_completed acknowledged; nothing here depends on it
        case .started(let total):
            guard total == files.count else { throw WorkerFailure(L10n.text("Воркер сообщил неверное число файлов.")) }
        case .log(let text):
            recordLog(text)
        case .fileStarted(let index, let file, let total):
            let index = try fileIndex(index, file: file, total: total)
            emit(.fileStarted(files[index], index, files.count))
            emit(.progress(Double(index) / Double(files.count), files[index].lastPathComponent))
        case .progress(let index, let file, let total, let stage, let message, let fileProgress):
            let index = try fileIndex(index, file: file, total: total)
            // Без `message` воркер шлёт только id стадии — показываем его название,
            // как PyQt (_STAGE_NAMES), а не «preprocessing».
            let text = safeText(message ?? StageLabel.text(stage, english: L10n.isEnglish))
            // `stage_progress: null` (conversion without a duration, diarization) only
            // means the stage is indeterminate; nil leaves the bar where it is.
            let fraction = fileProgress.map { (Double(index) + min(1, max(0, $0))) / Double(files.count) }
            emit(.progress(fraction, text))
        case .fileCompleted(let index, let file, let result):
            try acceptResult(result, index: fileIndex(index, file: file, total: nil, requireTotal: false))
        case .completed(let success, let cancelled, let results, let message):
            guard results.count <= files.count else { throw WorkerFailure(L10n.text("Воркер прислал неверное событие завершения.")) }
            // With compact_completed these are metadata only; every file already came
            // in full through file_completed and is skipped here.
            for (index, result) in results.enumerated() { try acceptResult(result, index: index) }
            if let message { recordLog(message) }
            if success && completedIndices.count != files.count { throw WorkerFailure(L10n.text("Воркер завершил пакет без результатов для всех файлов.")) }
            if !success && !cancelled && completedIndices.isEmpty {
                requestTerminal(.failed(failureDetails(message ?? L10n.text("Распознавание остановилось до обработки первого файла."))))
            } else {
                requestTerminal(.completed(success: success && !hadFileError, cancelled: cancelled))
            }
        case .error(let message, let traceback, let command):
            guard let text = message else { throw WorkerFailure(L10n.text("Воркер прислал ошибку без текста.")) }
            switch BatchErrorPolicy.disposition(command: command, helloReply: helloReply) {
            case .diagnostic:
                // A worker older than the handshake rejects `hello`; `start` follows.
                recordDiagnostic(text)
            case .logged:
                // Another command's answer, e.g. a `cancel` that crossed the end of the
                // batch: the files keep coming and `completed` decides the outcome.
                recordLog(text)
            case .fatal:
                recordLog(text)
                if let traceback { recordLog(traceback) }
                requestTerminal(.failed(safeText(text)))
            }
        }
    }

    private func fileIndex(_ index: Int, file: String, total: Int?, requireTotal: Bool = true) throws -> Int {
        guard files.indices.contains(index), URL(fileURLWithPath: file).standardizedFileURL == files[index],
              !requireTotal || total == files.count else {
            throw WorkerFailure(L10n.text("Воркер сослался на неизвестный файл."))
        }
        return index
    }

    private func acceptResult(_ result: BatchFileResult, index: Int) throws {
        guard files.indices.contains(index), URL(fileURLWithPath: result.filePath).standardizedFileURL == files[index] else {
            throw WorkerFailure(L10n.text("Воркер прислал неверный результат файла."))
        }
        // `completed` repeats the same results; do not re-read files or re-emit them.
        guard !completedIndices.contains(index) else { return }
        // Pull the traceback in before the failure is reported. Only stderr: this
        // runs inside the stdout callback, which must not be re-entered.
        if !result.success { worker?.drainDiagnostics() }
        let stem = files[index].deletingPathExtension().lastPathComponent
        var outputFiles: [String: URL] = [:]
        var errors = [result.error, result.diarizationError].compactMap { $0 }
        for savedPath in result.savedFiles {
            guard let root = runtimeRoot else { throw WorkerFailure(L10n.text("Нет данных о Python-окружении.")) }
            let directory = resolvedOutputDirectory ?? files[index].deletingLastPathComponent().resolvingSymlinksInPath().standardizedFileURL
            let reported = URL(fileURLWithPath: savedPath, relativeTo: root).standardizedFileURL
            let file = reported.resolvingSymlinksInPath().standardizedFileURL
            let expectedName = reported.lastPathComponent
            guard !savedPath.contains("\0"), file.deletingLastPathComponent().path == directory.path,
                  let format = OutputNaming.format(ofOutputNamed: expectedName, stem: stem),
                  let values = try? file.resourceValues(forKeys: [.isRegularFileKey]), values.isRegularFile == true,
                  FileManager.default.isReadableFile(atPath: file.path) else {
                errors.append(L10n.format("Воркер вернул недоступный или небезопасный файл результата: %@", savedPath))
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
                guard data.count <= limit else { throw WorkerFailure(L10n.text("Транскрипт больше 32 МиБ — откройте сохранённый файл.")) }
                guard let text = String(data: data, encoding: .utf8) else { throw WorkerFailure(L10n.text("Сохранённый транскрипт не в кодировке UTF-8.")) }
                transcript = text
                break
            } catch { errors.append(error.localizedDescription) }
        }
        if !result.success && errors.isEmpty { errors.append(failureDetails(L10n.text("Python-обработчик не смог распознать этот файл."))) }
        if result.success && outputFiles.isEmpty { errors.append(L10n.text("Python-обработчик не создал ни одного читаемого файла результата.")) }
        let error = errors.isEmpty ? nil : safeText(errors.joined(separator: "\n"))
        hadFileError = hadFileError || !result.success || error != nil
        completedIndices.insert(index)
        emit(.fileCompleted(NativeTranscriptionResult(inputURL: files[index], transcript: transcript,
                                                     outputFiles: outputFiles, error: error,
                                                     metadataJSON: result.metadataJSON)))
    }

    private func emit(_ event: NativeTranscriptionEvent) {
        guard !finished, pendingTerminal == nil else { return }
        DispatchQueue.main.async { self.onEvent(event) }
    }

    private func recordLog(_ text: String) {
        guard let text = recordDiagnostic(text) else { return }
        emit(.log(text))
    }

    /// Keeps the raw line for failure reports; returns it redacted, or nil when blank.
    @discardableResult
    private func recordDiagnostic(_ text: String) -> String? {
        guard !finished, pendingTerminal == nil else { return nil }
        let text = safeText(text).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        diagnostics = String((diagnostics + "\n" + text).suffix(16 * 1024))
        return text
    }

    private func safeText(_ text: String) -> String { WorkerRedaction.safeText(text, secrets: secrets) }

    private func failureDetails(_ message: String) -> String {
        let summary = safeText(message)
        return diagnostics.isEmpty ? summary : summary + "\n\n" + L10n.text("Технические подробности для отчёта об ошибке:") + "\n" + safeText(diagnostics)
    }

    private func requestTerminal(_ event: NativeTranscriptionEvent) {
        guard !finished, pendingTerminal == nil else { return }
        pendingTerminal = event
        // main() loops over stdin and its processing thread is a daemon. EOF is
        // the normal shutdown protocol, but only AFTER the batch has completed.
        guard let worker, worker.isRunning else { finish(); return }
        worker.closeInput()
        worker.terminateGracefully(after: 2)
    }

    private func workerExited(status: Int32) {
        guard !finished else { return }
        if pendingTerminal == nil {
            pendingTerminal = .failed(failureDetails(L10n.format("%@ завершился, не сообщив о завершении (код %@).", WorkerRole.transcription.name, String(status))))
        }
        finish()
    }

    private func finish() {
        guard !finished, let event = pendingTerminal else { return }
        finished = true
        worker?.close()
        worker = nil
        DispatchQueue.main.async { self.onEvent(event) }
    }
}
