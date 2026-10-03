import Foundation
import GigaAMLiquidCore
import Testing
@testable import GigaAMLiquid

@Suite struct LiveSessionJobTests {
    static let sessionDir = "/tmp/live/2026-10-03_12-00-00"
    static let recording = #"{"type":"live_status","session_dir":"\#(sessionDir)","state":"recording","active_sources":["mic"],"failed_sources":[]}"#
    static let stopped = #"{"type":"live_stopped","session_dir":"\#(sessionDir)","saved_files":["\#(sessionDir)/transcript.txt"],"recordings":{"mic":"\#(sessionDir)/mic.flac"}}"#

    /// Starts a session against `worker` and waits for `recording`.
    private func startedJob(_ worker: FakeWorker, log: EventLog<LiveSessionEvent>) async throws -> LiveSessionJob {
        let settings = LiveSessionSettings(sessionRoot: URL(fileURLWithPath: "/tmp/live"), sources: [.mic])
        let job = LiveSessionJob(settings: settings, makeCaptures: { [SilentCapture(sink: $0)] },
                                 runtime: worker.provider, onEvent: { log.append($0) })
        job.start()
        try await log.wait { if case .status(state: "recording", _, _, _) = $0 { return true } else { return false } }
        return job
    }

    private func answer(in log: EventLog<LiveSessionEvent>) async throws -> (status: String, text: String) {
        let event = try await log.wait { if case .answer = $0 { return true } else { return false } }
        guard case .answer(_, let status, let text) = event else { throw TimeoutError(description: "not an answer") }
        return (status, text)
    }

    /// The final answer replaces the streamed text on screen. It went through the
    /// log redaction: cut to its last 8192 characters, and prose like "Bearer
    /// token" or "token: …" rewritten to "[redacted]".
    @Test func finalAnswerIsNeitherTruncatedNorRedacted() async throws {
        let body = String(repeating: "Решение принято. ", count: 900)
        let text = "Bearer token передаётся в заголовке; token: из настроек; ключ sk-proj начинается так. " + body + "Конец."
        #expect(text.count > 8192)
        let reply = try JSONSerialization.data(withJSONObject: ["type": "live_answer", "turn_id": "t1", "status": "complete", "text": text])
        let worker = try FakeWorker(replies: [
            "live_start": [Self.recording, #"{"type":"live_final","event_id":"e1","source":"mic","sample_start":0,"sample_end":16000,"text":"Привет"}"#],
            "live_ask": [String(decoding: reply, as: UTF8.self)],
        ])
        let log = EventLog<LiveSessionEvent>()
        let job = try await startedJob(worker, log: log)
        job.ask("Что решили?", settings: ["provider": "API", "api_key": "sk-test-secret-123456"])
        let result = try await answer(in: log)
        #expect(result.status == "complete")
        #expect(result.text == text)
        job.terminate()
    }

    private func terminal(in log: EventLog<LiveSessionEvent>) async throws -> String {
        let event = try await log.wait { event in
            switch event {
            case .stopped, .failed: return true
            default: return false
            }
        }
        if case .failed(let message) = event { return "failed: \(message)" }
        return "stopped"
    }

    private func errorStatuses(in log: EventLog<LiveSessionEvent>) -> [String] {
        log.events.compactMap { if case .captureEvent(_, "error", let detail) = $0 { return detail } else { return nil } }
    }

    /// While stopping, any error used to end the job and throw away the
    /// live_stopped that followed — the UI said "failed" for a saved session.
    @Test func rejectedCommandWhileStoppingKeepsTheSavedSession() async throws {
        let worker = try FakeWorker(replies: [
            "live_start": [Self.recording],
            "live_stop": [#"{"type":"error","message":"live_audio pcm is not valid base64 int16"}"#, Self.stopped],
        ])
        let log = EventLog<LiveSessionEvent>()
        let job = try await startedJob(worker, log: log)
        job.stop()
        #expect(try await terminal(in: log) == "stopped")
    }

    private func stopped(in log: EventLog<LiveSessionEvent>) async throws -> (exports: [URL], recordings: [URL], outcome: LiveStopOutcome) {
        let event = try await log.wait { if case .stopped = $0 { return true } else { return false } }
        guard case .stopped(_, let exports, let recordings, let outcome) = event else { throw TimeoutError(description: "not stopped") }
        return (exports, recordings, outcome)
    }

    /// stop() finishes even when a stage fails and reports it in `message` next to
    /// what it saved. That read as "the session stopped with an error" over a
    /// saved transcript; and only the first FLAC of each source was listed.
    @Test func aStopThatSavedFilesDespiteAFailedStageIsAWarning() async throws {
        let dir = Self.sessionDir
        let partial = #"{"type":"live_stopped","session_dir":"\#(dir)","saved_files":["\#(dir)/transcript.txt"],"recordings":{"mic":"\#(dir)/mic.flac"},"recording_files":{"mic":["\#(dir)/mic.flac","\#(dir)/mic-002.flac"]},"message":"diarize: RuntimeError: boom"}"#
        let worker = try FakeWorker(replies: ["live_start": [Self.recording], "live_stop": [partial]])
        let log = EventLog<LiveSessionEvent>()
        let job = try await startedJob(worker, log: log)
        job.stop()
        let result = try await stopped(in: log)
        #expect(result.outcome == .savedWithWarning("diarize: RuntimeError: boom"))
        #expect(result.exports.map(\.lastPathComponent) == ["transcript.txt"])
        #expect(result.recordings.map(\.lastPathComponent) == ["mic.flac", "mic-002.flac"])
    }

    @Test func aStopThatSavedNothingFails() async throws {
        let failed = #"{"type":"live_stopped","session_dir":"\#(Self.sessionDir)","saved_files":[],"recordings":{},"message":"session is not running"}"#
        let worker = try FakeWorker(replies: ["live_start": [Self.recording], "live_stop": [failed]])
        let log = EventLog<LiveSessionEvent>()
        let job = try await startedJob(worker, log: log)
        job.stop()
        #expect(try await stopped(in: log).outcome == .failed("session is not running"))
    }

    /// A question before the first final is answered with the worker's reason,
    /// not shown as a raw English status line.
    @Test func rejectedQuestionIsAnsweredAndTheSessionGoesOn() async throws {
        let worker = try FakeWorker(replies: [
            "live_start": [Self.recording],
            "live_ask": [#"{"type":"error","message":"No final transcript events are available yet"}"#],
            "live_stop": [Self.stopped],
        ])
        let log = EventLog<LiveSessionEvent>()
        let job = try await startedJob(worker, log: log)
        job.ask("Что решили?", settings: ["provider": "API"])
        let result = try await answer(in: log)
        #expect(result.status == "rejected")
        #expect(result.text == "No final transcript events are available yet")
        #expect(errorStatuses(in: log).isEmpty)
        job.stop()
        #expect(try await terminal(in: log) == "stopped")
    }

    /// Any error the worker names as live_ask's answers the question. By text,
    /// "already running" was only logged, and the answer field kept saying the
    /// assistant was answering, with Ask disabled, until the session ended.
    @Test func everyRejectionOfANamedQuestionAnswersIt() async throws {
        let worker = try FakeWorker(replies: [
            "live_start": [Self.recording],
            "live_ask": [#"{"type":"error","message":"An assistant question is already running","command":"live_ask"}"#],
            "live_stop": [Self.stopped],
        ])
        let log = EventLog<LiveSessionEvent>()
        let job = try await startedJob(worker, log: log)
        job.ask("Что решили?", settings: ["provider": "API"])
        let result = try await answer(in: log)
        #expect(result.status == "rejected")
        #expect(result.text == "An assistant question is already running")
        job.stop()
        #expect(try await terminal(in: log) == "stopped")
    }

    /// A rejected pause is a notice for the log; the session and its stop go on.
    @Test func aNamedPauseRejectionKeepsTheSession() async throws {
        let worker = try FakeWorker(replies: [
            "live_start": [Self.recording],
            "live_pause": [#"{"type":"error","message":"Session is already paused","command":"live_pause"}"#],
            "live_stop": [Self.stopped],
        ])
        let log = EventLog<LiveSessionEvent>()
        let job = try await startedJob(worker, log: log)
        job.pause()
        try await log.wait { if case .log("Session is already paused") = $0 { return true } else { return false } }
        job.stop()
        #expect(try await terminal(in: log) == "stopped")
    }

    @Test func cancellingAFinishedAnswerChangesNothing() async throws {
        let worker = try FakeWorker(replies: [
            "live_start": [Self.recording],
            "live_ask_cancel": [#"{"type":"error","message":"No assistant question is running"}"#],
            "live_stop": [Self.stopped],
        ])
        let log = EventLog<LiveSessionEvent>()
        let job = try await startedJob(worker, log: log)
        job.cancelAsk()
        job.stop()
        #expect(try await terminal(in: log) == "stopped")
        #expect(errorStatuses(in: log).isEmpty)
    }

    @Test func failedAnswerIsStillRedacted() async throws {
        let worker = try FakeWorker(replies: [
            "live_start": [Self.recording],
            "live_ask": [#"{"type":"live_answer","turn_id":"t1","status":"error","text":"401 for key sk-test-secret-123456"}"#],
        ])
        let log = EventLog<LiveSessionEvent>()
        let job = try await startedJob(worker, log: log)
        job.ask("Что решили?", settings: ["provider": "API", "api_key": "sk-test-secret-123456"])
        let result = try await answer(in: log)
        #expect(result.status == "error")
        #expect(!result.text.contains("sk-test-secret-123456"))
        #expect(result.text.contains("[redacted]"))
        job.terminate()
    }
}
