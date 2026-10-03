import Foundation
import GigaAMLiquidCore

/// Short-lived worker round-trip: `llm_tools` (scan every CLI provider) or
/// `llm_tool_check` (re-probe one). Exactly one completion callback; the worker
/// is closed as soon as the answer arrives.
final class LLMToolsQuery {
    enum Result {
        case tools(providers: [String], tools: [LLMToolStatus])
        case tool(LLMToolStatus)
        case failed(String)
    }

    private let command: [String: Any]
    private let onResult: (Result) -> Void
    private let queue = DispatchQueue(label: "GigaAMLiquid.llm-tools", qos: .userInitiated)
    private var worker: WorkerProcess?
    private var finished = false
    private var secrets: [String] = []
    /// Worker stderr, stray stdout and unknown events, on the main queue. Without
    /// it a failing probe (a broken CLI, a Python traceback) left no trace at all.
    var onLog: ((String) -> Void)?

    private let resolveRuntime: PythonRuntime.Provider

    static func scan(overrides: [String: String], fresh: Bool, runtime: @escaping PythonRuntime.Provider = PythonRuntime.resolveDefault,
                     onResult: @escaping (Result) -> Void) -> LLMToolsQuery {
        LLMToolsQuery(command: ["type": "llm_tools", "overrides": overrides, "fresh": fresh], runtime: runtime, onResult: onResult)
    }

    static func check(provider: String, path: String, runtime: @escaping PythonRuntime.Provider = PythonRuntime.resolveDefault,
                      onResult: @escaping (Result) -> Void) -> LLMToolsQuery {
        LLMToolsQuery(command: ["type": "llm_tool_check", "provider": provider, "path": path], runtime: runtime, onResult: onResult)
    }

    private init(command: [String: Any], runtime: @escaping PythonRuntime.Provider, onResult: @escaping (Result) -> Void) {
        self.command = command
        self.resolveRuntime = runtime
        self.onResult = onResult
    }

    func start() {
        queue.async {
            guard self.worker == nil, !self.finished else { return }
            do { try self.launch() } catch { self.finish(.failed(error.localizedDescription)) }
        }
    }

    func cancel() {
        queue.async {
            guard !self.finished else { return }
            self.finished = true
            self.worker?.closeInput()
            self.worker?.terminateGracefully(after: 1)
        }
    }

    private func launch() throws {
        let runtime = try resolveRuntime()
        secrets = WorkerRedaction.secrets(in: runtime.environment)
        let worker = try WorkerProcess(
            runtime: runtime, arguments: runtime.transcriptionArguments, environment: runtime.environment, queue: queue,
            onLine: { self.consume($0) },
            onStderr: { self.log($0) },
            onStdoutEnd: { self.finish(.failed("The worker closed its output before answering.")) },
            onError: { self.finish(.failed($0)) },
            onExit: { status in
                self.finish(.failed("The worker exited without answering (status \(status))."))
                self.releaseWorker()
            }
        )
        self.worker = worker
        try worker.send(command)
    }

    private func consume(_ line: Data) {
        guard !finished else { return }
        switch LLMToolsDecoder.decode(line) {
        case nil:
            return
        case .text(let text):
            log(text)
        case .unknown(let type):
            log(L10n.format("Пропущено неизвестное событие воркера: %@", type))
        case .invalid:
            finish(.failed("Malformed llm_tool_check reply."))
        case .event(.tools(let providers, let tools)):
            finish(.tools(providers: providers, tools: tools))
        case .event(.tool(let tool)):
            finish(.tool(tool))
        case .event(.error(let message)):
            finish(.failed(WorkerRedaction.safeText(message ?? "LLM tools query failed.", secrets: secrets)))
        case .event(.log(let text)):
            log(text)
        }
    }

    private func log(_ text: String) {
        let line = WorkerRedaction.safeText(text, secrets: secrets).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !line.isEmpty, let onLog else { return }
        DispatchQueue.main.async { onLog(line) }
    }

    private func finish(_ result: Result) {
        guard !finished else { return }
        finished = true
        worker?.closeInput()
        worker?.terminateGracefully(after: 1)
        DispatchQueue.main.async { self.onResult(result) }
    }

    /// The worker's line readers hold this query through their callbacks; once the
    /// process is gone, drop them so the query is freed.
    private func releaseWorker() {
        worker?.close()
        worker = nil
    }
}
