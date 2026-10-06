import Foundation
@testable import GigaAMLiquid

/// A scripted stand-in for the Python worker (src.tui_worker): a /bin/sh loop that
/// records every JSONL command, answers each command type with the lines
/// registered for it, and exits when its stdin closes — the worker's shutdown
/// protocol. No Python, no models.
final class FakeWorker {
    let directory: URL

    /// `replies[type]` is written verbatim after a command of that type arrives.
    /// Types in `exitAfter` make the worker exit right after replying.
    init(replies: [String: [String]], exitAfter: Set<String> = []) throws {
        let manager = FileManager.default
        directory = manager.temporaryDirectory.appendingPathComponent("FakeWorker-\(UUID().uuidString)", isDirectory: true)
            .resolvingSymlinksInPath()
        try manager.createDirectory(at: directory, withIntermediateDirectories: true)
        let script = """
        #!/bin/sh
        dir="$(cd "$(dirname "$0")" && pwd)"
        while IFS= read -r line; do
          printf '%s\\n' "$line" >> "$dir/received.jsonl"
          type=$(printf '%s' "$line" | grep -o '"type":"[a-z_]*"' | head -n 1 | cut -d '"' -f 4)
          if [ -f "$dir/reply-$type.jsonl" ]; then cat "$dir/reply-$type.jsonl"; fi
          if [ -f "$dir/exit-$type" ]; then exit 0; fi
        done
        """
        let executable = directory.appendingPathComponent("worker.sh")
        try script.write(to: executable, atomically: true, encoding: .utf8)
        try manager.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        for (type, lines) in replies {
            try (lines.joined(separator: "\n") + "\n").write(to: directory.appendingPathComponent("reply-\(type).jsonl"),
                                                             atomically: true, encoding: .utf8)
        }
        for type in exitAfter {
            try Data().write(to: directory.appendingPathComponent("exit-\(type)"))
        }
    }

    deinit { try? FileManager.default.removeItem(at: directory) }

    var runtime: PythonRuntime {
        PythonRuntime(root: directory, executable: directory.appendingPathComponent("worker.sh"),
                      environment: ["PATH": "/usr/bin:/bin"], frozenCompanion: true, workingDirectory: directory)
    }

    var provider: PythonRuntime.Provider {
        let runtime = self.runtime
        return { runtime }
    }

    /// Commands the job sent, in order.
    var received: [[String: Any]] {
        guard let text = try? String(contentsOf: directory.appendingPathComponent("received.jsonl"), encoding: .utf8) else { return [] }
        return text.split(separator: "\n").compactMap {
            try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any]
        }
    }

    var receivedTypes: [String] { received.compactMap { $0["type"] as? String } }
}

/// Events delivered on the main queue, collected for assertions.
final class EventLog<Event> {
    private let lock = NSLock()
    private var storage: [Event] = []

    func append(_ event: Event) {
        lock.lock()
        storage.append(event)
        lock.unlock()
    }

    var events: [Event] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    /// Waits until an event matches; returns it.
    @discardableResult
    func wait(timeout: TimeInterval = 10, for predicate: (Event) -> Bool) async throws -> Event {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if let match = events.first(where: predicate) { return match }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        throw TimeoutError(description: "no matching event within \(timeout) s; got \(events)")
    }
}

struct TimeoutError: Error, CustomStringConvertible {
    let description: String
}

/// Polls `condition` until it holds or `timeout` passes.
func eventually(timeout: TimeInterval = 10, _ condition: () -> Bool) async throws -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if condition() { return true }
        try await Task.sleep(nanoseconds: 20_000_000)
    }
    return condition()
}

/// A capture source that produces nothing; tracks whether it is running.
final class SilentCapture: LiveCaptureSource {
    let source: LiveSource
    let sink: (LiveCaptureEvent) -> Void
    private(set) var started = false
    private(set) var stopped = false

    init(source: LiveSource = .mic, sink: @escaping (LiveCaptureEvent) -> Void) {
        self.source = source
        self.sink = sink
    }

    func start() throws { started = true }
    func pause() {}
    func resume() {}
    func stop() { stopped = true }
}
