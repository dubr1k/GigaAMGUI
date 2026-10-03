import Foundation
import Testing
@testable import GigaAMLiquid

/// NativeTranscriptionJob against the scripted worker. Event lines follow what
/// src.tui_worker really prints (captured from a run on tests/fixtures/liquid_smoke.wav).
@Suite struct TranscriptionJobTests {
    /// An input file with its already "saved" transcript next to it.
    private struct Batch {
        let input: URL
        let transcript: URL

        init(in worker: FakeWorker, name: String = "clip") throws {
            input = worker.directory.appendingPathComponent("\(name).wav")
            transcript = worker.directory.appendingPathComponent("\(name).txt")
            try Data("RIFF".utf8).write(to: input)
            try "Тестовый транскрипт".write(to: transcript, atomically: true, encoding: .utf8)
        }

        var fileStarted: String {
            #"{"type": "file_started", "file": "\#(input.path)", "file_index": 0, "total_files": 1}"#
        }

        func progress(stage: String, stageProgress: String, fileProgress: String) -> String {
            #"{"type": "progress", "file": "\#(input.path)", "file_index": 0, "total_files": 1, "stage": "\#(stage)", "stage_progress": \#(stageProgress), "file_progress": \#(fileProgress), "processed_seconds": null, "total_seconds": null, "message": null}"#
        }

        var fileCompleted: String {
            #"{"type": "file_completed", "file": "\#(input.path)", "file_index": 0, "result": {"success": true, "file_path": "\#(input.path)", "media_duration": 3.9, "diarization": {"requested": false, "applied": false, "backend": "pyannote", "error": null}, "saved_files": ["\#(transcript.path)"], "utterances": [{"transcription": "Тестовый транскрипт", "boundaries": [0.0, 3.7]}]}}"#
        }

        /// `completed` as the worker sends it to a client that negotiated
        /// compact_completed: per-file metadata only.
        var compactCompleted: String {
            #"{"type": "completed", "success": true, "cancelled": false, "results": [{"file_path": "\#(input.path)", "success": true, "error": null, "saved_files": ["\#(transcript.path)"]}], "elapsed_seconds": 13.8}"#
        }

        var startReply: [String] {
            [#"{"type": "started", "files": ["\#(input.path)"], "total_files": 1, "backend": "auto"}"#,
             #"{"type": "log", "message": "Загружаем модель распознавания речи…"}"#,
             fileStarted, fileCompleted, compactCompleted]
        }
    }

    private static let ready = #"{"type": "ready", "protocol_version": 1, "capabilities": ["resolve_inputs", "asr", "llm"]}"#

    private func run(_ worker: FakeWorker, _ batch: Batch) async throws -> EventLog<NativeTranscriptionEvent> {
        let log = EventLog<NativeTranscriptionEvent>()
        let job = NativeTranscriptionJob(files: [batch.input], outputDirectory: nil, settings: NativeTranscriptionSettings(),
                                         runtime: worker.provider) { log.append($0) }
        job.start()
        try await log.wait { event in
            switch event {
            case .completed, .failed: return true
            default: return false
            }
        }
        return log
    }

    private func outcome(_ log: EventLog<NativeTranscriptionEvent>) -> String {
        for event in log.events {
            switch event {
            case .completed(let success, let cancelled): return "completed(success: \(success), cancelled: \(cancelled))"
            case .failed(let message): return "failed: \(message)"
            default: continue
            }
        }
        return "none"
    }

    private func logs(_ log: EventLog<NativeTranscriptionEvent>) -> [String] {
        log.events.compactMap { if case .log(let text) = $0 { return text } else { return nil } }
    }

    @Test func negotiatesCompactCompletionBeforeStarting() async throws {
        let worker = try FakeWorker(replies: [:])
        let batch = try Batch(in: worker)
        try (Self.ready + "\n").write(to: worker.directory.appendingPathComponent("reply-hello.jsonl"), atomically: true, encoding: .utf8)
        try (batch.startReply.joined(separator: "\n") + "\n").write(to: worker.directory.appendingPathComponent("reply-start.jsonl"), atomically: true, encoding: .utf8)
        let log = try await run(worker, batch)
        #expect(outcome(log) == "completed(success: true, cancelled: false)")
        #expect(worker.receivedTypes.prefix(2) == ["hello", "start"])
        let hello = worker.received.first
        #expect(hello?["client"] as? String == "liquid")
        #expect(hello?["features"] as? [String] == ["compact_completed"])
        // file_completed stays full: the transcript and the JSON tab come from it.
        let result = log.events.compactMap { if case .fileCompleted(let result) = $0 { return result } else { return nil } }.first
        #expect(result?.transcript == "Тестовый транскрипт")
        #expect(result?.metadataJSON.contains("utterances") == true)
        #expect(result?.error == nil)
    }

    /// A worker older than `hello` answers it with an error; that is not a failed batch.
    @Test func olderWorkerRejectingHelloStillRunsTheBatch() async throws {
        let worker = try FakeWorker(replies: [:])
        let batch = try Batch(in: worker)
        try (#"{"type": "error", "message": "Unknown command: 'hello'"}"# + "\n")
            .write(to: worker.directory.appendingPathComponent("reply-hello.jsonl"), atomically: true, encoding: .utf8)
        try (batch.startReply.joined(separator: "\n") + "\n").write(to: worker.directory.appendingPathComponent("reply-start.jsonl"), atomically: true, encoding: .utf8)
        let log = try await run(worker, batch)
        #expect(outcome(log) == "completed(success: true, cancelled: false)")
    }

    /// No `ready` at all must neither block the batch nor fail it.
    @Test func missingReadyDoesNotBlock() async throws {
        let worker = try FakeWorker(replies: [:])
        let batch = try Batch(in: worker)
        try (batch.startReply.joined(separator: "\n") + "\n").write(to: worker.directory.appendingPathComponent("reply-start.jsonl"), atomically: true, encoding: .utf8)
        let log = try await run(worker, batch)
        #expect(outcome(log) == "completed(success: true, cancelled: false)")
    }

    /// The worker is shared with the TUI and grows new events; dependencies print
    /// to stdout. Neither may abort a batch whose files are being saved.
    @Test func unknownEventsAndStrayStdoutAreLoggedNotFatal() async throws {
        let worker = try FakeWorker(replies: [:])
        let batch = try Batch(in: worker)
        var reply = batch.startReply
        reply.insert(#"{"type": "inputs_resolved", "request_id": "r1", "files": []}"#, at: 2)
        reply.insert(#"{'loaded': True, 'device': 'mps'}"#, at: 3)
        reply.insert(#"[1, 2, 3]"#, at: 4)
        try (Self.ready + "\n").write(to: worker.directory.appendingPathComponent("reply-hello.jsonl"), atomically: true, encoding: .utf8)
        try (reply.joined(separator: "\n") + "\n").write(to: worker.directory.appendingPathComponent("reply-start.jsonl"), atomically: true, encoding: .utf8)
        let log = try await run(worker, batch)
        #expect(outcome(log) == "completed(success: true, cancelled: false)")
        #expect(logs(log).contains { $0.contains("'loaded': True") })
        #expect(logs(log).contains { $0.contains("inputs_resolved") })
    }
}
