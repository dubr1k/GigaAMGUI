import Foundation
import Testing
@testable import GigaAMLiquid

/// Every worker-backed job must be freed once its worker has exited. The worker's
/// line readers call back into the job, so a job that keeps its WorkerProcess
/// after the process is gone keeps itself alive — and a Live job keeps its
/// AVAudioEngine with it. Each LLM request, Live session and LLM page visit
/// used to leak one.
@Suite struct JobLifetimeTests {
    @Test func finishedLLMJobIsFreed() async throws {
        let worker = try FakeWorker(replies: ["llm_start": [
            #"{"type":"llm_started","mode":"custom","index":1,"total":1}"#,
            #"{"type":"llm_chunk","mode":"custom","text":"Ответ"}"#,
            #"{"type":"llm_completed","success":true,"cancelled":false,"results":[{"mode":"custom","text":"Ответ"}],"saved_files":[]}"#,
        ]])
        let log = EventLog<LLMJobEvent>()
        weak var released: LLMJob?
        do {
            let job = LLMJob(request: LLMRequest(text: "текст", modes: ["custom"], prompt: "p", settings: [:]),
                             runtime: worker.provider) { log.append($0) }
            released = job
            job.start()
            try await log.wait { if case .completed = $0 { return true } else { return false } }
        }
        #expect(try await eventually { released == nil }, "LLMJob outlived its worker")
    }

    @Test func answeredToolsQueryIsFreed() async throws {
        let worker = try FakeWorker(replies: ["llm_tools": [
            #"{"type":"llm_tools","providers":["API","Claude Code"],"tools":[{"id":"claude","provider":"Claude Code","status":"missing","install_hint":"npm i -g @anthropic-ai/claude-code"}]}"#,
        ]])
        let log = EventLog<LLMToolsQuery.Result>()
        weak var released: LLMToolsQuery?
        do {
            let query = LLMToolsQuery.scan(overrides: [:], fresh: false, runtime: worker.provider) { log.append($0) }
            released = query
            query.start()
            try await log.wait { if case .tools = $0 { return true } else { return false } }
        }
        #expect(try await eventually { released == nil }, "LLMToolsQuery outlived its worker")
    }

    @Test func stoppedLiveSessionIsFreedWithItsCaptures() async throws {
        let worker = try FakeWorker(replies: [
            "live_start": [
                #"{"type":"live_loading","message":"Loading the recognition model"}"#,
                #"{"type":"live_status","session_dir":"/tmp/live/2026-10-03_12-00-00","state":"recording","active_sources":["mic"],"failed_sources":[]}"#,
            ],
            "live_stop": [
                #"{"type":"live_status","session_dir":"/tmp/live/2026-10-03_12-00-00","state":"stopped","active_sources":[],"failed_sources":[]}"#,
                #"{"type":"live_stopped","session_dir":"/tmp/live/2026-10-03_12-00-00","saved_files":["/tmp/live/2026-10-03_12-00-00/transcript.txt"],"recordings":{"mic":"/tmp/live/2026-10-03_12-00-00/mic.flac"}}"#,
            ],
        ])
        let log = EventLog<LiveSessionEvent>()
        weak var released: LiveSessionJob?
        weak var capture: SilentCapture?
        do {
            let settings = LiveSessionSettings(sessionRoot: URL(fileURLWithPath: "/tmp/live"), sources: [.mic])
            let job = LiveSessionJob(settings: settings, makeCaptures: { sink in
                let created = SilentCapture(sink: sink)
                capture = created
                return [created]
            }, runtime: worker.provider, onEvent: { log.append($0) })
            released = job
            job.start()
            try await log.wait { if case .status(state: "recording", _, _, _) = $0 { return true } else { return false } }
            #expect(capture?.started == true, "capture starts on live_status: recording")
            job.stop()
            try await log.wait { if case .stopped = $0 { return true } else { return false } }
            #expect(capture?.stopped == true)
        }
        #expect(try await eventually { released == nil }, "LiveSessionJob outlived its worker")
        #expect(capture == nil, "the capture (an AVAudioEngine in the app) outlived the session")
    }
}
