import Foundation
import GigaAMLiquidCore

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
    /// The worker is loading the recognition model; capture has not started yet.
    case loading
    /// `sessionDir` is where this session's transcript and audio are being written.
    case status(state: String, active: [LiveSource], failed: [LiveSource], sessionDir: URL?)
    case partial(id: String, source: LiveSource, sampleStart: Int, text: String)
    case final(id: String, source: LiveSource, sampleStart: Int, sampleEnd: Int, text: String, speaker: String?)
    case level(LiveSource, Float)
    case captureEvent(source: LiveSource, kind: String, detail: String)
    case answerChunk(turnID: String, text: String)
    case answer(turnID: String, status: String, text: String)
    /// `error` is set when the worker stopped the session but could not finish saving it.
    case stopped(sessionDir: URL, saved: [URL], error: String?)
    case failed(String)
    case log(String)
}

/// Owns the worker for one live session and forwards captured PCM to it.
/// Capture sources call `handleCapture` from their audio threads; everything
/// else is serialized on `queue`, and exactly one terminal event is delivered.
final class LiveSessionJob {
    private let settings: LiveSessionSettings
    /// Built by the owner's factory with a weak back-reference: captures must not
    /// keep the job (and its worker and audio engine) alive after the session.
    private var captures: [LiveCaptureSource] = []
    private let onEvent: (LiveSessionEvent) -> Void
    private let queue = DispatchQueue(label: "GigaAMLiquid.live", qos: .userInitiated)
    private var worker: WorkerProcess?
    private var finished = false
    private var stopping = false
    /// Set by the first `live_status`; until then any worker error means live_start was rejected.
    private var sessionReported = false
    /// Capture starts on the first `recording` status, i.e. once the model is ready.
    private var capturing = false
    private var secrets: [String] = []
    private let backlogLock = NSLock()
    private var backlog = 0
    /// 50 chunks × 100 ms = 5 s per source; beyond that the worker is not keeping up.
    private let maxBufferedChunks = 50

    /// `makeCaptures` receives the sink for captured audio; it holds the job weakly.
    private let resolveRuntime: PythonRuntime.Provider

    init(settings: LiveSessionSettings,
         makeCaptures: (@escaping (LiveCaptureEvent) -> Void) -> [LiveCaptureSource],
         runtime: @escaping PythonRuntime.Provider = PythonRuntime.resolveDefault,
         onEvent: @escaping (LiveSessionEvent) -> Void) {
        self.settings = settings
        self.resolveRuntime = runtime
        self.onEvent = onEvent
        captures = makeCaptures { [weak self] event in self?.handleCapture(event) }
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
        let runtime = try resolveRuntime()
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
            onExit: { status in
                self.finish(.failed("The live worker exited without stopping (status \(status))."))
                self.releaseWorker()
            }
        )
        self.worker = worker
        try worker.send([
            "type": "live_start", "session_root": settings.sessionRoot.path, "sources": settings.sources.map(\.rawValue),
            "sample_rate": 16_000, "diarization_mode": settings.diarizationMode, "diarization_backend": settings.diarizationBackend,
            "record_mic": settings.recordMic, "record_system": settings.recordSystem, "exports": settings.exports,
            "backend": settings.backend, "model": settings.model, "onnx_provider": settings.onnxProvider
        ])
        // Captures wait for `live_status: recording`. Started here, they fed the
        // worker while it was still loading the model: the first phrase was not
        // recognised until the load finished, and a long load overflowed the
        // 5 s client backlog and dropped audio outright.
    }

    private func startCaptures() {
        guard !capturing, !stopping, !finished else { return }
        capturing = true
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
            sessionReported = true
            let active = (object["active_sources"] as? [String] ?? []).compactMap(LiveSource.init(rawValue:))
            let failed = (object["failed_sources"] as? [String] ?? []).compactMap(LiveSource.init(rawValue:))
            let directory = (object["session_dir"] as? String).map { URL(fileURLWithPath: $0, isDirectory: true) }
            emit(.status(state: object["state"] as? String ?? "", active: active, failed: failed, sessionDir: directory))
            if object["state"] as? String == "recording" { startCaptures() }
        case "live_loading":
            emit(.loading)
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
            // A completed answer is the user's content and replaces the streamed text:
            // the log redaction would cut it to its last 8 KiB and rewrite prose such
            // as "Bearer token". Only an error message is a diagnostic.
            let status = object["status"] as? String ?? ""
            let text = object["text"] as? String ?? ""
            emit(.answer(turnID: object["turn_id"] as? String ?? "", status: status,
                         text: status == "error" ? safe(text) : text))
        case "live_stopped":
            let exports = object["saved_files"] as? [String] ?? []
            let recordings = (object["recordings"] as? [String: String] ?? [:]).sorted { $0.key < $1.key }.map(\.value)
            let saved = (exports + recordings).map { URL(fileURLWithPath: $0) }
            let directory = URL(fileURLWithPath: object["session_dir"] as? String ?? settings.sessionRoot.path)
            // The worker reports a failed stop as live_stopped plus a message; showing
            // it only in the log left the status claiming the session was saved.
            let message = (object["message"] as? String).map(safe).flatMap { $0.isEmpty ? nil : $0 }
            if let message { emit(.log(message)) }
            finish(.stopped(sessionDir: directory, saved: saved, error: message))
        case "error":
            let raw = object["message"] as? String ?? "Live worker error."
            let message = safe(raw)
            // Before the first live_status the only thing we sent was live_start, so an error
            // (rejected settings, an old companion without live support, …) is terminal.
            // Afterwards errors concern single commands (a bad chunk, a second pause, a
            // question too early) — also while stopping, where ending the job here threw
            // away the live_stopped that follows.
            switch LiveErrorPolicy.disposition(of: raw, sessionReported: sessionReported) {
            case .fatal: finish(.failed(message))
            case .questionRejected: emit(.answer(turnID: "", status: "rejected", text: message))
            case .ignorable: break
            case .logged: emit(.log(message))
            }
        case "log":
            emit(.log(safe(object["message"] as? String ?? "")))
        default:
            // The worker is shared with the TUI and gains events over time.
            emit(.log(L10n.format("Пропущено неизвестное событие воркера: %@", type)))
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

    /// The worker's line readers hold this job through their callbacks; once the
    /// process is gone, drop them so the job, its captures and the audio engine go.
    private func releaseWorker() {
        worker?.close()
        worker = nil
    }
}
