import Foundation

struct LLMRequest {
    var text: String
    var files: [URL] = []
    var modes: [String]
    var prompt: String = ""
    var settings: [String: Any]
    var outputDirectory: URL?
}

enum LLMJobEvent {
    case started(mode: String, index: Int, total: Int)
    case chunk(mode: String, text: String)
    case completed(results: [(mode: String, text: String)], saved: [URL])
    case cancelled
    case failed(String)
    case log(String)
}

/// One worker per request; exactly one terminal event (completed/cancelled/failed).
final class LLMJob {
    private let request: LLMRequest
    private let onEvent: (LLMJobEvent) -> Void
    private let queue = DispatchQueue(label: "GigaAMLiquid.llm", qos: .userInitiated)
    private var worker: WorkerProcess?
    private var finished = false
    private var secrets: [String] = []

    init(request: LLMRequest, onEvent: @escaping (LLMJobEvent) -> Void) {
        self.request = request
        self.onEvent = onEvent
    }

    func start() {
        queue.async {
            guard self.worker == nil, !self.finished else { return }
            do { try self.launch() } catch { self.finish(.failed(self.safe(error.localizedDescription))) }
        }
    }

    func cancel() {
        queue.async {
            guard let worker = self.worker, !self.finished else { return }
            try? worker.send(["type": "llm_cancel"])
        }
    }

    /// Synchronous teardown for application exit.
    func terminate() {
        queue.sync {
            guard !self.finished else { return }
            self.worker?.kill()
            self.finish(.cancelled)
        }
    }

    private func launch() throws {
        let runtime = try PythonRuntime.resolve()
        if let key = request.settings["api_key"] as? String, key.count >= 6 { secrets.append(key) }
        secrets.append(contentsOf: WorkerRedaction.secrets(in: runtime.environment))
        var command: [String: Any] = [
            "type": "llm_start", "text": request.text, "files": request.files.map(\.path),
            "modes": request.modes, "prompt": request.prompt, "settings": request.settings
        ]
        if let directory = request.outputDirectory { command["output_dir"] = directory.path }
        let worker = try WorkerProcess(
            runtime: runtime, arguments: runtime.transcriptionArguments, environment: runtime.environment, queue: queue,
            onLine: { self.consume($0) },
            onStderr: { self.emit(.log(self.safe($0))) },
            onStdoutEnd: { self.finish(.failed("The LLM worker closed its output without completing.")) },
            onError: { self.finish(.failed($0)) },
            onExit: { status in self.finish(.failed("The LLM worker exited without completing (status \(status)).")) }
        )
        self.worker = worker
        try worker.send(command)
    }

    private func consume(_ line: Data) {
        guard !finished, !line.isEmpty else { return }
        guard let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
              let type = object["type"] as? String else {
            let text = String(decoding: line, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty { emit(.log(safe(text))) }
            return
        }
        switch type {
        case "llm_started":
            emit(.started(mode: object["mode"] as? String ?? "", index: object["index"] as? Int ?? 0, total: object["total"] as? Int ?? 0))
        case "llm_chunk":
            emit(.chunk(mode: object["mode"] as? String ?? "", text: object["text"] as? String ?? ""))
        case "llm_completed":
            if object["cancelled"] as? Bool == true { finish(.cancelled); return }
            guard object["success"] as? Bool == true else {
                finish(.failed(safe(object["message"] as? String ?? "LLM request failed.")))
                return
            }
            let results = (object["results"] as? [[String: Any]] ?? []).map {
                (mode: $0["mode"] as? String ?? "", text: $0["text"] as? String ?? "")
            }
            let saved = (object["saved_files"] as? [String] ?? []).map { URL(fileURLWithPath: $0) }
            finish(.completed(results: results, saved: saved))
        case "error":
            finish(.failed(safe(object["message"] as? String ?? "LLM worker error.")))
        case "log":
            emit(.log(safe(object["message"] as? String ?? "")))
        default:
            break
        }
    }

    private func safe(_ text: String) -> String { WorkerRedaction.safeText(text, secrets: secrets) }

    private func emit(_ event: LLMJobEvent) {
        guard !finished else { return }
        DispatchQueue.main.async { self.onEvent(event) }
    }

    private func finish(_ event: LLMJobEvent) {
        guard !finished else { return }
        finished = true
        worker?.closeInput()
        worker?.terminateGracefully(after: 2)
        DispatchQueue.main.async { self.onEvent(event) }
    }
}
