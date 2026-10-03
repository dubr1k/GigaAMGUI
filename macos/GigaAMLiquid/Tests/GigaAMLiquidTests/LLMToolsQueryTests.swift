import Foundation
import Testing
@testable import GigaAMLiquid

@Suite struct LLMToolsQueryTests {
    /// The query used to drop stray stdout, unknown events and all of stderr, so a
    /// probe that failed inside the worker left nothing to diagnose it by.
    @Test func workerChatterReachesTheLog() async throws {
        let worker = try FakeWorker(replies: ["llm_tool_check": [
            #"Traceback-ish chatter on stdout"#,
            #"{"type": "inputs_resolved", "files": []}"#,
            #"{"type": "llm_tool_check", "tool": {"id": "pi", "provider": "Pi", "status": "broken", "path": "/opt/pi", "detail": "exit 1", "install_hint": "npm i -g pi"}}"#,
        ]])
        let results = EventLog<LLMToolsQuery.Result>()
        let logs = EventLog<String>()
        let query = LLMToolsQuery.check(provider: "Pi", path: "/opt/pi", runtime: worker.provider) { results.append($0) }
        query.onLog = { logs.append($0) }
        query.start()
        let result = try await results.wait { _ in true }
        guard case .tool(let status) = result else { Issue.record("unexpected \(result)"); return }
        #expect(status.status == "broken")
        #expect(logs.events.contains("Traceback-ish chatter on stdout"))
        #expect(logs.events.contains { $0.contains("inputs_resolved") })
    }
}
