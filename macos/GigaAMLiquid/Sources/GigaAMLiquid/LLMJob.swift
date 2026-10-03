import Foundation
import GigaAMLiquidCore

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

    private let resolveRuntime: PythonRuntime.Provider

    init(request: LLMRequest, runtime: @escaping PythonRuntime.Provider = PythonRuntime.resolveDefault,
         onEvent: @escaping (LLMJobEvent) -> Void) {
        self.request = request
        self.resolveRuntime = runtime
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
        let runtime = try resolveRuntime()
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
            onExit: { status in
                self.finish(.failed("The LLM worker exited without completing (status \(status))."))
                self.releaseWorker()
            }
        )
        self.worker = worker
        try worker.send(command)
    }

    private func consume(_ line: Data) {
        guard !finished else { return }
        switch LLMEventDecoder.decode(line) {
        case nil:
            return
        case .text(let text):
            emit(.log(safe(text)))
        case .unknown(let type), .invalid(let type):
            // The worker is shared with the TUI and gains events over time.
            emit(.log(L10n.format("Пропущено неизвестное событие воркера: %@", type)))
        case .event(.started(let mode, let index, let total)):
            emit(.started(mode: mode, index: index, total: total))
        case .event(.chunk(let mode, let text)):
            emit(.chunk(mode: mode, text: text))
        case .event(.completed(let results, let saved)):
            finish(.completed(results: results, saved: saved.map { URL(fileURLWithPath: $0) }))
        case .event(.cancelled):
            finish(.cancelled)
        case .event(.failed(let message)):
            finish(.failed(safe(message ?? "LLM request failed.")))
        case .event(.error(let message)):
            finish(.failed(safe(message ?? "LLM worker error.")))
        case .event(.log(let text)):
            emit(.log(safe(text)))
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

    /// The worker's line readers hold this job through their callbacks; once the
    /// process is gone, drop them so the job is freed.
    private func releaseWorker() {
        worker?.close()
        worker = nil
    }
}
