import CoreFoundation
import Foundation

/// Typed accessors with the worker protocol's strictness: NSNumber bridges JSON
/// booleans and numbers, so a `true` must not pass for 1 or the other way round.
public enum WorkerJSON {
    public static func boolean(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }

    /// A non-negative integral number (indices, counts).
    public static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite, number.doubleValue >= 0,
              number.doubleValue < Double(Int.max), number.doubleValue.rounded(.down) == number.doubleValue else { return nil }
        return number.intValue
    }

    /// A finite number that is not a boolean.
    public static func number(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite else { return nil }
        return number.doubleValue
    }

    public static func isAbsent(_ value: Any?) -> Bool { value == nil || value is NSNull }
}

/// One stdout line of a worker, before its meaning is known.
public enum WorkerLine {
    /// A protocol message: a JSON object with a string `type`.
    case message(type: String, object: [String: Any])
    /// Anything else on stdout — a dependency's print, a Python repr such as
    /// "{'loaded': True}", a JSON value without `type`. Log material.
    case text(String)

    /// nil for a blank line.
    public static func decode(_ line: Data) -> WorkerLine? {
        if let object = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any],
           let type = object["type"] as? String {
            return .message(type: type, object: object)
        }
        let text = String(decoding: line, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : .text(text)
    }
}

/// How a line of a particular worker conversation decodes.
public enum WorkerDecoded<Event> {
    case event(Event)
    case text(String)
    /// A message type this client does not know; the worker is shared with the TUI
    /// and gains events, so this is never an error by itself.
    case unknown(type: String)
    /// A known event whose payload breaks the protocol.
    case invalid(type: String)
}

// MARK: - Batch transcription (start → … → completed)

/// One file's result as `file_completed` (full) or a compact `completed` reports it.
public struct BatchFileResult {
    public let filePath: String
    public let success: Bool
    public let error: String?
    public let diarizationError: String?
    public let savedFiles: [String]
    private let raw: [String: Any]

    public init?(_ object: [String: Any]) {
        guard let success = WorkerJSON.boolean(object["success"]), let path = object["file_path"] as? String,
              let saved = object["saved_files"] as? [String] else { return nil }
        filePath = path
        self.success = success
        savedFiles = saved
        error = (object["error"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        diarizationError = ((object["diarization"] as? [String: Any])?["error"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        raw = object
    }

    /// The result as the worker sent it, for the Result page's JSON tab. Computed on
    /// demand: a duplicate in `completed` is skipped before anyone needs it.
    public var metadataJSON: String {
        guard let data = try? JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys]) else { return "" }
        return String(decoding: data, as: UTF8.self)
    }
}

public enum BatchEvent {
    /// The reply to `hello`.
    case ready(capabilities: [String])
    case started(totalFiles: Int?)
    /// `log` and `cancelling`.
    case log(String)
    case fileStarted(index: Int, file: String, totalFiles: Int?)
    /// `fileProgress` is nil when the worker sent none or null.
    case progress(index: Int, file: String, totalFiles: Int?, stage: String, message: String?, fileProgress: Double?)
    case fileCompleted(index: Int, file: String, result: BatchFileResult)
    case completed(success: Bool, cancelled: Bool, results: [BatchFileResult], message: String?)
    /// `command` names the command the error answers; an older worker sends none.
    case error(message: String?, traceback: String?, command: String?)
}

public enum BatchEventDecoder {
    public static func decode(_ line: Data) -> WorkerDecoded<BatchEvent>? {
        switch WorkerLine.decode(line) {
        case nil: return nil
        case .text(let text): return .text(text)
        case .message(let type, let object): return decode(type: type, object)
        }
    }

    static func decode(type: String, _ object: [String: Any]) -> WorkerDecoded<BatchEvent> {
        let index = WorkerJSON.integer(object["file_index"])
        let file = object["file"] as? String
        switch type {
        case "ready":
            return .event(.ready(capabilities: object["capabilities"] as? [String] ?? []))
        case "started":
            return .event(.started(totalFiles: WorkerJSON.integer(object["total_files"])))
        case "log", "cancelling":
            guard let text = object["message"] as? String else { return .invalid(type: type) }
            return .event(.log(text))
        case "file_started":
            guard let index, let file else { return .invalid(type: type) }
            return .event(.fileStarted(index: index, file: file, totalFiles: WorkerJSON.integer(object["total_files"])))
        case "progress":
            guard let index, let file, let stage = object["stage"] as? String else { return .invalid(type: type) }
            // `stage_progress: null` only says the stage is indeterminate; the file's
            // own monotonic progress is still in `file_progress`.
            let fileProgress = WorkerJSON.number(object["file_progress"])
            guard fileProgress != nil || WorkerJSON.isAbsent(object["file_progress"]) else { return .invalid(type: type) }
            return .event(.progress(index: index, file: file, totalFiles: WorkerJSON.integer(object["total_files"]),
                                    stage: stage, message: object["message"] as? String, fileProgress: fileProgress))
        case "file_completed":
            guard let index, let file, let raw = object["result"] as? [String: Any],
                  let result = BatchFileResult(raw) else { return .invalid(type: type) }
            return .event(.fileCompleted(index: index, file: file, result: result))
        case "completed":
            guard let success = WorkerJSON.boolean(object["success"]), let cancelled = WorkerJSON.boolean(object["cancelled"]),
                  let raws = object["results"] as? [[String: Any]] else { return .invalid(type: type) }
            let results = raws.compactMap(BatchFileResult.init)
            guard results.count == raws.count else { return .invalid(type: type) }
            return .event(.completed(success: success, cancelled: cancelled, results: results, message: object["message"] as? String))
        case "error":
            return .event(.error(message: object["message"] as? String, traceback: object["traceback"] as? String,
                                 command: object["command"] as? String))
        default:
            return .unknown(type: type)
        }
    }
}

// MARK: - Live session

public enum LiveWorkerEvent {
    case loading
    case status(state: String, active: [String], failed: [String], sessionDir: String?)
    case partial(id: String, source: String, sampleStart: Int, text: String)
    case final(id: String, source: String, sampleStart: Int, sampleEnd: Int, text: String, speaker: String?)
    case captureEvent(source: String, kind: String, detail: String)
    case answerChunk(turnID: String, text: String)
    case answer(turnID: String, status: String, text: String)
    /// `recordings`: every recording segment, tracks by name, segments in order.
    /// `message` is set when a stage of the stop failed — with files saved or none.
    case stopped(sessionDir: String?, savedFiles: [String], recordings: [String], message: String?)
    /// `command` names the live_* command the error answers; an older worker sends none.
    case error(message: String?, command: String?)
    case log(String)
}

/// How a `live_stopped` reads to the user.
public enum LiveStopOutcome: Equatable {
    /// Every stage finished.
    case saved
    /// The stop finished and wrote files, but a stage failed (`message`): the
    /// session is saved, only not completely — e.g. after-stop diarization.
    case savedWithWarning(String)
    /// Nothing was saved; the message says why.
    case failed(String)

    /// The worker's stop() runs every stage even when one fails and reports the
    /// failures in `message` next to whatever it saved.
    public init(message: String?, savedFiles: [String], recordings: [String]) {
        guard let message else { self = .saved; return }
        self = savedFiles.isEmpty && recordings.isEmpty ? .failed(message) : .savedWithWarning(message)
    }
}

public enum LiveEventDecoder {
    public static func decode(_ line: Data) -> WorkerDecoded<LiveWorkerEvent>? {
        switch WorkerLine.decode(line) {
        case nil: return nil
        case .text(let text): return .text(text)
        case .message(let type, let object):
            guard let event = decode(type: type, object) else { return .unknown(type: type) }
            return .event(event)
        }
    }

    static func decode(type: String, _ object: [String: Any]) -> LiveWorkerEvent? {
        let source = object["source"] as? String ?? "mic"
        switch type {
        case "live_loading":
            return .loading
        case "live_status":
            return .status(state: object["state"] as? String ?? "", active: object["active_sources"] as? [String] ?? [],
                           failed: object["failed_sources"] as? [String] ?? [], sessionDir: object["session_dir"] as? String)
        case "live_partial":
            return .partial(id: object["event_id"] as? String ?? "", source: source,
                            sampleStart: object["sample_start"] as? Int ?? 0, text: object["text"] as? String ?? "")
        case "live_final":
            return .final(id: object["event_id"] as? String ?? "", source: source,
                          sampleStart: object["sample_start"] as? Int ?? 0, sampleEnd: object["sample_end"] as? Int ?? 0,
                          text: object["text"] as? String ?? "", speaker: object["speaker"] as? String)
        case "live_capture_event":
            return .captureEvent(source: source, kind: object["kind"] as? String ?? "", detail: object["detail"] as? String ?? "")
        case "live_answer_chunk":
            return .answerChunk(turnID: object["turn_id"] as? String ?? "", text: object["text"] as? String ?? "")
        case "live_answer":
            return .answer(turnID: object["turn_id"] as? String ?? "", status: object["status"] as? String ?? "",
                           text: object["text"] as? String ?? "")
        case "live_stopped":
            // A long session rolls each track over to further FLACs: `recording_files`
            // lists every segment, the mix included; `recordings` (an older worker)
            // only the first file of each source.
            let recordings: [String]
            if let segments = object["recording_files"] as? [String: [String]] {
                recordings = segments.sorted { $0.key < $1.key }.flatMap(\.value)
            } else {
                recordings = (object["recordings"] as? [String: String] ?? [:]).sorted { $0.key < $1.key }.map(\.value)
            }
            let message = (object["message"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            return .stopped(sessionDir: object["session_dir"] as? String, savedFiles: object["saved_files"] as? [String] ?? [],
                            recordings: recordings, message: message)
        case "error":
            return .error(message: object["message"] as? String, command: object["command"] as? String)
        case "log":
            return .log(object["message"] as? String ?? "")
        default:
            return nil
        }
    }
}

// MARK: - LLM request

public enum LLMWorkerEvent {
    case started(mode: String, index: Int, total: Int)
    case chunk(mode: String, text: String)
    case completed(results: [(mode: String, text: String)], savedFiles: [String])
    case cancelled
    /// `llm_completed` with success false.
    case failed(String?)
    case error(String?)
    case log(String)
}

public enum LLMEventDecoder {
    public static func decode(_ line: Data) -> WorkerDecoded<LLMWorkerEvent>? {
        switch WorkerLine.decode(line) {
        case nil: return nil
        case .text(let text): return .text(text)
        case .message(let type, let object):
            switch type {
            case "llm_started":
                return .event(.started(mode: object["mode"] as? String ?? "", index: object["index"] as? Int ?? 0,
                                       total: object["total"] as? Int ?? 0))
            case "llm_chunk":
                return .event(.chunk(mode: object["mode"] as? String ?? "", text: object["text"] as? String ?? ""))
            case "llm_completed":
                if WorkerJSON.boolean(object["cancelled"]) == true { return .event(.cancelled) }
                guard WorkerJSON.boolean(object["success"]) == true else { return .event(.failed(object["message"] as? String)) }
                let results = (object["results"] as? [[String: Any]] ?? []).map {
                    (mode: $0["mode"] as? String ?? "", text: $0["text"] as? String ?? "")
                }
                return .event(.completed(results: results, savedFiles: object["saved_files"] as? [String] ?? []))
            case "error":
                return .event(.error(object["message"] as? String))
            case "log":
                return .event(.log(object["message"] as? String ?? ""))
            default:
                return .unknown(type: type)
            }
        }
    }
}

// MARK: - LLM CLI tools

/// One CLI provider's discovery result, as reported by the Python registry
/// (`src/services/cli_tools.py`). The native app never looks for binaries itself:
/// the worker runs with the same extended PATH that the LLM job will use, so what
/// it finds is exactly what will run.
public struct LLMToolStatus: Equatable {
    public let id: String
    public let provider: String
    public let status: String      // found | missing | broken | not_applicable
    public let path: String?
    public let version: String?
    public let detail: String?
    public let installHint: String

    public init?(_ object: [String: Any]) {
        guard let id = object["id"] as? String, let provider = object["provider"] as? String,
              let status = object["status"] as? String else { return nil }
        self.id = id
        self.provider = provider
        self.status = status
        path = object["path"] as? String
        version = object["version"] as? String
        detail = object["detail"] as? String
        installHint = object["install_hint"] as? String ?? ""
    }

    public var dictionary: [String: Any] {
        var object: [String: Any] = ["id": id, "provider": provider, "status": status, "install_hint": installHint]
        if let path { object["path"] = path }
        if let version { object["version"] = version }
        if let detail { object["detail"] = detail }
        return object
    }
}

public enum LLMToolsEvent {
    case tools(providers: [String], tools: [LLMToolStatus])
    case tool(LLMToolStatus)
    case error(String?)
    case log(String)
}

public enum LLMToolsDecoder {
    public static func decode(_ line: Data) -> WorkerDecoded<LLMToolsEvent>? {
        switch WorkerLine.decode(line) {
        case nil: return nil
        case .text(let text): return .text(text)
        case .message(let type, let object):
            switch type {
            case "llm_tools":
                return .event(.tools(providers: object["providers"] as? [String] ?? [],
                                     tools: (object["tools"] as? [[String: Any]] ?? []).compactMap(LLMToolStatus.init)))
            case "llm_tool_check":
                guard let tool = (object["tool"] as? [String: Any]).flatMap(LLMToolStatus.init) else { return .invalid(type: type) }
                return .event(.tool(tool))
            case "error":
                return .event(.error(object["message"] as? String))
            case "log":
                return .event(.log(object["message"] as? String ?? ""))
            default:
                return .unknown(type: type)
            }
        }
    }
}
