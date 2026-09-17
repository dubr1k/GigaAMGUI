import Foundation

struct LiveSessionSettings {
    var sessionRoot: URL
    var sources: [LiveSource]
    var microphoneDeviceID: String?
    var diarizationMode = "off"
    var diarizationBackend = "pyannote"
    var recordMic = true
    var recordSystem = true
    var exports: [String: Any] = ["txt": true]
    var backend = "auto"
    var model = "v3_e2e_rnnt"
    var onnxProvider = "auto"
    var hfToken: String?
}

enum LiveSessionEvent {
    case status(state: String, active: [LiveSource], failed: [LiveSource])
    case partial(id: String, source: LiveSource, sampleStart: Int, text: String)
    case final(id: String, source: LiveSource, sampleStart: Int, sampleEnd: Int, text: String, speaker: String?)
    case level(LiveSource, Float)
    case captureEvent(source: LiveSource, kind: String, detail: String)
    case answerChunk(turnID: String, text: String)
    case answer(turnID: String, status: String, text: String)
    case stopped(sessionDir: URL, saved: [URL])
    case failed(String)
    case log(String)
}

/// Owns the worker for one live session and forwards captured PCM to it.
/// Capture sources call `handleCapture` from their audio threads; everything
/// else is serialized on `queue`, and exactly one terminal event is delivered.
final class LiveSessionJob {
    private let settings: LiveSessionSettings
    private let captures: [LiveCaptureSource]
    private let onEvent: (LiveSessionEvent) -> Void
    private let queue = DispatchQueue(label: "GigaAMLiquid.live", qos: .userInitiated)
    private var worker: WorkerProcess?
    private var finished = false
    private var stopping = false
    private var secrets: [String] = []
    private let backlogLock = NSLock()
    private var backlog = 0
    /// 50 chunks × 100 ms = 5 s per source; beyond that the worker is not keeping up.
    private let maxBufferedChunks = 50

    init(settings: LiveSessionSettings, captures: [LiveCaptureSource], onEvent: @escaping (LiveSessionEvent) -> Void) {
        self.settings = settings
        self.captures = captures
        self.onEvent = onEvent
    }

    func start() {
        queue.async {
            guard self.worker == nil, !self.finished else { return }
            do { try self.launch() } catch { self.finish(.failed(self.safe(error.localizedDescription))) }
        }
    }

    func pause() {
        queue.async {
            guard !self.finished else { return }
            self.captures.forEach { $0.pause() }
            try? self.worker?.send(["type": "live_pause"])
        }
    }

    func resume() {
        queue.async {
            guard !self.finished else { return }
            self.captures.forEach { $0.resume() }
            try? self.worker?.send(["type": "live_resume"])
        }
    }

    func stop() {
        queue.async {
            guard !self.finished, !self.stopping else { return }
            self.stopping = true
            // Stopping a source flushes its trailing chunk synchronously through handleCapture,
            // which enqueues after this block; the worker receives it before live_stop.
            self.captures.forEach { $0.stop() }
            self.queue.async {
                do { try self.worker?.send(["type": "live_stop"]) }
                catch { self.finish(.failed(self.safe(error.localizedDescription))) }
            }
        }
    }

    func ask(_ question: String, settings: [String: Any]) {
        queue.async {
            guard !self.finished else { return }
            if let key = settings["api_key"] as? String, key.count >= 6, !self.secrets.contains(key) { self.secrets.append(key) }
            do { try self.worker?.send(["type": "live_ask", "question": question, "settings": settings]) }
            catch { self.emit(.answer(turnID: "", status: "error", text: self.safe(error.localizedDescription))) }
        }
    }

    func cancelAsk() {
        queue.async { try? self.worker?.send(["type": "live_ask_cancel"]) }
    }

    /// Synchronous teardown for application exit; loses the in-flight session.
    func terminate() {
        queue.sync {
            guard !self.finished else { return }
            self.captures.forEach { $0.stop() }
            self.worker?.kill()
            self.finish(.failed(L10n.text("Live-сессия прервана.")))
        }
    }

    // MARK: - Capture → worker

    func handleCapture(_ event: LiveCaptureEvent) {
        switch event {
        case .chunk(let chunk):
            backlogLock.lock()
            let queued = backlog
            if queued < maxBufferedChunks { backlog += 1 }
            backlogLock.unlock()
            guard queued < maxBufferedChunks else {
                queue.async {
                    guard !self.finished else { return }
                    self.emit(.captureEvent(source: chunk.source, kind: "overflow", detail: L10n.text("Worker не успевает обрабатывать звук; фрагмент пропущен.")))
                    try? self.worker?.send(["type": "live_capture_event", "source": chunk.source.rawValue, "kind": "overflow", "detail": "client backlog"])
                }
                return
            }
            queue.async {
                defer {
                    self.backlogLock.lock()
                    self.backlog -= 1
                    self.backlogLock.unlock()
                }
                guard !self.finished else { return }
                try? self.worker?.send([
                    "type": "live_audio", "source": chunk.source.rawValue, "seq": chunk.seq,
                    "sample_offset": chunk.sampleOffset, "timestamp_ns": chunk.timestampNs,
                    "pcm": chunk.pcm.base64EncodedString()
                ])
            }
        case .level(let source, let rms):
            emit(.level(source, rms))
        case .permissionDenied(let source, let detail):
            forwardCaptureEvent(source: source, kind: "permission_denied", detail: detail)
        case .deviceRemoved(let source, let detail):
            forwardCaptureEvent(source: source, kind: "device_removed", detail: detail)
        case .overflow(let source, let detail):
            forwardCaptureEvent(source: source, kind: "overflow", detail: detail)
        }
    }

    private func forwardCaptureEvent(source: LiveSource, kind: String, detail: String) {
        queue.async {
            guard !self.finished else { return }
            try? self.worker?.send(["type": "live_capture_event", "source": source.rawValue, "kind": kind, "detail": detail])
        }
    }

    // MARK: - Worker → UI

    private func launch() throws {
        let runtime = try PythonRuntime.resolve()
        var environment = runtime.environment
        if let token = settings.hfToken?.trimmingCharacters(in: .whitespacesAndNewlines), !token.isEmpty {
            environment["HF_TOKEN"] = token
        }
        secrets = WorkerRedaction.secrets(in: environment)
        let worker = try WorkerProcess(
            runtime: runtime, arguments: runtime.transcriptionArguments, environment: environment, queue: queue,
            onLine: { self.consume($0) },
            onStderr: { self.emit(.log(self.safe($0))) },
            onStdoutEnd: { self.finish(.failed("The live worker closed its output without stopping.")) },
            onError: { self.finish(.failed($0)) },
            onExit: { status in self.finish(.failed("The live worker exited without stopping (status \(status)).")) }
        )
        self.worker = worker
        try worker.send([
            "type": "live_start", "session_root": settings.sessionRoot.path, "sources": settings.sources.map(\.rawValue),
            "sample_rate": 16_000, "diarization_mode": settings.diarizationMode, "diarization_backend": settings.diarizationBackend,
            "record_mic": settings.recordMic, "record_system": settings.recordSystem, "exports": settings.exports,
            "backend": settings.backend, "model": settings.model, "onnx_provider": settings.onnxProvider
        ])
        for capture in captures {
            do { try capture.start() }
            catch { handleCapture(.permissionDenied(capture.source, error.localizedDescription)) }
        }
    }

    private func consume(_ line: Data) {
        guard !finished, !line.isEmpty else { return }
        guard let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
              let type = object["type"] as? String else {
            let text = String(decoding: line, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty { emit(.log(safe(text))) }
            return
        }
        let source = LiveSource(rawValue: object["source"] as? String ?? "") ?? .mic
        switch type {
        case "live_status":
            let active = (object["active_sources"] as? [String] ?? []).compactMap(LiveSource.init(rawValue:))
            let failed = (object["failed_sources"] as? [String] ?? []).compactMap(LiveSource.init(rawValue:))
            emit(.status(state: object["state"] as? String ?? "", active: active, failed: failed))
        case "live_partial":
            emit(.partial(id: object["event_id"] as? String ?? "", source: source,
                          sampleStart: object["sample_start"] as? Int ?? 0, text: object["text"] as? String ?? ""))
        case "live_final":
            emit(.final(id: object["event_id"] as? String ?? "", source: source,
                        sampleStart: object["sample_start"] as? Int ?? 0, sampleEnd: object["sample_end"] as? Int ?? 0,
                        text: object["text"] as? String ?? "", speaker: object["speaker"] as? String))
        case "live_capture_event":
            emit(.captureEvent(source: source, kind: object["kind"] as? String ?? "", detail: safe(object["detail"] as? String ?? "")))
        case "live_answer_chunk":
            emit(.answerChunk(turnID: object["turn_id"] as? String ?? "", text: object["text"] as? String ?? ""))
        case "live_answer":
            emit(.answer(turnID: object["turn_id"] as? String ?? "", status: object["status"] as? String ?? "",
                         text: safe(object["text"] as? String ?? "")))
        case "live_stopped":
            let saved = (object["saved_files"] as? [String] ?? []).map { URL(fileURLWithPath: $0) }
            let directory = URL(fileURLWithPath: object["session_dir"] as? String ?? settings.sessionRoot.path)
            if let message = object["message"] as? String, !message.isEmpty { emit(.log(safe(message))) }
            finish(.stopped(sessionDir: directory, saved: saved))
        case "error":
            let message = safe(object["message"] as? String ?? "Live worker error.")
            // Before the session is running, an error means live_start was rejected.
            if stopping || worker?.isRunning != true { finish(.failed(message)) }
            else if message.hasPrefix("Could not start live session") || message == "Processing is already running" || message.hasPrefix("session_root") {
                finish(.failed(message))
            } else {
                emit(.log(message))
            }
        case "log":
            emit(.log(safe(object["message"] as? String ?? "")))
        default:
            break
        }
    }

    private func safe(_ text: String) -> String { WorkerRedaction.safeText(text, secrets: secrets) }

    private func emit(_ event: LiveSessionEvent) {
        guard !finished else { return }
        DispatchQueue.main.async { self.onEvent(event) }
    }

    private func finish(_ event: LiveSessionEvent) {
        guard !finished else { return }
        finished = true
        captures.forEach { $0.stop() }
        worker?.closeInput()
        worker?.terminateGracefully(after: 2)
        DispatchQueue.main.async { self.onEvent(event) }
    }
}
