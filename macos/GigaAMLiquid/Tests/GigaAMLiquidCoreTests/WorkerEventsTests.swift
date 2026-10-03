import Foundation
import Testing
@testable import GigaAMLiquidCore

private func data(_ line: String) -> Data { Data(line.utf8) }

@Suite struct WorkerLineTests {
    @Test func strayStdoutIsTextNotAnError() {
        guard case .text(let repr) = WorkerLine.decode(data("{'loaded': True}")) else { Issue.record("dict repr"); return }
        #expect(repr == "{'loaded': True}")
        guard case .text = WorkerLine.decode(data("[1, 2]")) else { Issue.record("bare JSON value"); return }
        guard case .text = WorkerLine.decode(data(#"{"no_type": 1}"#)) else { Issue.record("object without type"); return }
        #expect(WorkerLine.decode(data("   ")) == nil)
    }

    @Test func strictJSONValues() {
        #expect(WorkerJSON.boolean(NSNumber(value: true)) == true)
        #expect(WorkerJSON.boolean(NSNumber(value: 1)) == nil)
        #expect(WorkerJSON.integer(NSNumber(value: true)) == nil)
        #expect(WorkerJSON.integer(NSNumber(value: 2.5)) == nil)
        #expect(WorkerJSON.integer(NSNumber(value: -1)) == nil)
        #expect(WorkerJSON.integer(NSNumber(value: 3)) == 3)
        #expect(WorkerJSON.number(NSNumber(value: 0.12)) == 0.12)
        #expect(WorkerJSON.number(NSNumber(value: false)) == nil)
    }
}

@Suite struct BatchEventDecoderTests {
    private func event(_ line: String) -> BatchEvent? {
        if case .event(let event) = BatchEventDecoder.decode(data(line)) { return event }
        return nil
    }

    @Test func realBatchRunDecodes() throws {
        guard case .ready(let capabilities) = event(WorkerFixtures.ready) else { Issue.record("ready"); return }
        #expect(capabilities.contains("asr"))
        guard case .started(let total) = event(WorkerFixtures.started) else { Issue.record("started"); return }
        #expect(total == 1)
        guard case .log(let text) = event(WorkerFixtures.log) else { Issue.record("log"); return }
        #expect(text == "Загружаем модель распознавания речи…")
        guard case .fileStarted(0, "/tmp/fixture/clip.wav", 1) = event(WorkerFixtures.fileStarted) else { Issue.record("file_started"); return }
        guard case .progress(0, _, 1, "preparing", nil, 0.0) = event(WorkerFixtures.progressPreparing) else { Issue.record("progress"); return }
        guard case .progress(_, _, _, "transcription", nil, let fraction) = event(WorkerFixtures.progressNullSeconds) else { Issue.record("progress null seconds"); return }
        #expect(fraction == 0.95)
    }

    @Test func fileResultsAreFullAndCompletionMayBeCompact() {
        guard case .fileCompleted(0, _, let result) = event(WorkerFixtures.fileCompleted) else { Issue.record("file_completed"); return }
        #expect(result.success && result.error == nil && result.diarizationError == nil)
        #expect(result.savedFiles == ["/tmp/fixture/clip.txt", "/tmp/fixture/clip.srt"])
        #expect(result.metadataJSON.contains("\"utterances\""))
        for line in [WorkerFixtures.completedFull, WorkerFixtures.completedCompact] {
            guard case .completed(true, false, let results, nil) = event(line) else { Issue.record("completed"); return }
            #expect(results.map(\.filePath) == ["/tmp/fixture/clip.wav"])
            #expect(results.first?.savedFiles.count == 2)
        }
        #expect(WorkerFixtures.completedCompact.utf8.count < WorkerFixtures.completedFull.utf8.count / 5)
    }

    @Test func nullStageProgressKeepsTheFileProgress() {
        let line = #"{"type": "progress", "file": "/a.wav", "file_index": 0, "total_files": 1, "stage": "diarization", "stage_progress": null, "file_progress": 0.8, "message": null}"#
        guard case .progress(_, _, _, "diarization", _, 0.8) = event(line) else { Issue.record("diarization progress"); return }
        let missing = #"{"type": "progress", "file": "/a.wav", "file_index": 0, "stage": "export", "file_progress": null}"#
        guard case .progress(_, _, nil, "export", _, nil) = event(missing) else { Issue.record("null file progress"); return }
    }

    @Test func brokenKnownEventsAreInvalidAndNewOnesUnknown() {
        guard case .invalid("progress") = BatchEventDecoder.decode(data(#"{"type": "progress", "file": "/a.wav", "file_index": 0, "stage": "x", "file_progress": "half"}"#)) else { Issue.record("string progress"); return }
        guard case .invalid("file_completed") = BatchEventDecoder.decode(data(#"{"type": "file_completed", "file": "/a.wav", "file_index": 0, "result": {"success": 1}}"#)) else { Issue.record("bad result"); return }
        guard case .invalid("file_started") = BatchEventDecoder.decode(data(#"{"type": "file_started", "file": "/a.wav", "file_index": true}"#)) else { Issue.record("bool index"); return }
        guard case .unknown("inputs_resolved") = BatchEventDecoder.decode(data(#"{"type": "inputs_resolved", "files": []}"#)) else { Issue.record("unknown"); return }
    }
}

@Suite struct LiveEventDecoderTests {
    private func event(_ line: String) -> LiveWorkerEvent? {
        if case .event(let event) = LiveEventDecoder.decode(data(line)) { return event }
        return nil
    }

    @Test func realLiveSessionDecodes() {
        guard case .loading = event(WorkerFixtures.liveLoading) else { Issue.record("loading"); return }
        guard case .status("recording", ["mic"], [], let dir) = event(WorkerFixtures.liveRecording) else { Issue.record("recording"); return }
        #expect(dir == "/tmp/fixture/live/2026-10-03_17-21-49")
        guard case .status("stopped", [], [], _) = event(WorkerFixtures.liveStoppedState) else { Issue.record("stopped state"); return }
        guard case .partial("mic-0", "mic", 0, let draft) = event(WorkerFixtures.livePartial) else { Issue.record("partial"); return }
        #expect(draft.hasPrefix("Testing"))
        guard case .final("mic-0", "mic", 0, 109_413, _, nil) = event(WorkerFixtures.liveFinal) else { Issue.record("final"); return }
        guard case .error("No assistant question is running") = event(WorkerFixtures.liveError) else { Issue.record("error"); return }
        guard case .log(let log) = event(WorkerFixtures.liveLog) else { Issue.record("log"); return }
        #expect(log.hasPrefix("session start"))
        guard case .stopped(_, let saved, let recordings, nil) = event(WorkerFixtures.liveStopped) else { Issue.record("live_stopped"); return }
        #expect(saved.contains { $0.hasSuffix("transcript.txt") })
        #expect(recordings.count == 1)
    }

    /// The decoder hands the answer over as sent; redaction is the job's decision.
    @Test func answersArriveVerbatim() {
        guard case .answer("conversation-0", "complete", let text) = event(WorkerFixtures.liveAnswer) else { Issue.record("answer"); return }
        #expect(text.contains("О чём запись?"))
        #expect(text.contains("\n"))
    }

    @Test func unknownLiveEventsAreReported() {
        guard case .unknown("live_level") = LiveEventDecoder.decode(data(#"{"type": "live_level", "rms": 0.1}"#)) else { Issue.record("unknown"); return }
    }
}

@Suite struct LLMDecoderTests {
    @Test func realLLMRequestDecodes() {
        guard case .event(.started("custom", 1, 1)) = LLMEventDecoder.decode(data(WorkerFixtures.llmStarted)) else { Issue.record("started"); return }
        guard case .event(.completed(let results, let saved)) = LLMEventDecoder.decode(data(WorkerFixtures.llmCompleted)) else { Issue.record("completed"); return }
        #expect(results.map(\.mode) == ["custom"])
        #expect(results.first?.text.contains("Привет, мир.") == true)
        #expect(saved.isEmpty)
        guard case .event(.cancelled) = LLMEventDecoder.decode(data(#"{"type": "llm_completed", "success": false, "cancelled": true}"#)) else { Issue.record("cancelled"); return }
        guard case .event(.failed("boom")) = LLMEventDecoder.decode(data(#"{"type": "llm_completed", "success": false, "message": "boom"}"#)) else { Issue.record("failed"); return }
    }

    @Test func realToolRegistryDecodes() {
        guard case .event(.tools(let providers, let tools)) = LLMToolsDecoder.decode(data(WorkerFixtures.llmTools)) else { Issue.record("tools"); return }
        #expect(providers == ["API", "Claude Code", "Codex", "OpenCode", "Pi", "oh-my-pi", "Other"])
        #expect(tools.map(\.provider).contains("Claude Code"))
        guard case .event(.tool(let pi)) = LLMToolsDecoder.decode(data(WorkerFixtures.llmToolCheck)) else { Issue.record("check"); return }
        #expect(pi.status == "missing" && pi.path == nil && pi.installHint.contains("pi-coding-agent"))
        #expect(LLMToolStatus(pi.dictionary) == pi)
        guard case .invalid("llm_tool_check") = LLMToolsDecoder.decode(data(#"{"type": "llm_tool_check", "tool": {}}"#)) else { Issue.record("invalid"); return }
    }
}
