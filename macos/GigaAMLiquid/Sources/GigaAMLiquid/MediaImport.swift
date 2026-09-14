import Foundation

/// One real downloader invocation. Start once; completion is always delivered on the main queue.
final class MediaDownloadJob {
    private struct DownloadResponse: Decodable {
        let files: [String]
    }

    enum Failure: LocalizedError {
        case cancelled
        case message(String)

        var errorDescription: String? {
            switch self {
            case .cancelled: return L10n.text("Загрузка отменена.")
            case .message(let text): return text
            }
        }
    }

    private final class OutputTail {
        private let limit: Int
        private var data = Data()

        init(limit: Int) { self.limit = limit }

        // Each pipe has its own reader. Its data is consumed only after the readers join.
        func drain(_ handle: FileHandle) {
            defer { try? handle.close() }
            while let chunk = try? handle.read(upToCount: 8192), !chunk.isEmpty {
                data.append(chunk)
                if data.count > limit { data.removeFirst(data.count - limit) }
            }
        }

        var text: String { String(decoding: data, as: UTF8.self) }
    }

    private let url: URL
    private let completion: (Result<[URL], Error>) -> Void
    private let lock = NSLock()
    private var process: Process?
    private var cancelled = false
    private var started = false

    init(url: URL, completion: @escaping (Result<[URL], Error>) -> Void) {
        self.url = url
        self.completion = completion
    }

    static func validatedURL(_ text: String) -> URL? {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty,
              !value.contains(where: { $0.isWhitespace || $0.isNewline }),
              let components = URLComponents(string: value),
              let scheme = components.scheme?.lowercased(),
              ["http", "https"].contains(scheme),
              let host = components.host, !host.isEmpty,
              let url = components.url else { return nil }
        return url
    }

    func start() {
        lock.lock()
        guard !started else { lock.unlock(); return }
        started = true
        lock.unlock()
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Result { try self.download() }
            DispatchQueue.main.async {
                // Cancellation can arrive after the subprocess exits but before delivery.
                if self.isCancelled {
                    if case .success(let download) = result {
                        try? FileManager.default.removeItem(at: download.directory)
                    }
                    self.completion(.failure(Failure.cancelled))
                } else {
                    self.completion(result.map { $0.files })
                }
            }
        }
    }

    func cancel() {
        lock.lock()
        cancelled = true
        if let process, process.isRunning { process.terminate() }
        lock.unlock()
    }

    private var isCancelled: Bool {
        lock.lock()
        defer { lock.unlock() }
        return cancelled
    }

    private func download() throws -> (files: [URL], directory: URL) {
        guard Self.validatedURL(url.absoluteString) != nil else {
            throw Failure.message(L10n.text("Введите корректную ссылку http:// или https:// с именем сервера."))
        }
        if isCancelled { throw Failure.cancelled }
        let manager = FileManager.default
        let runtime = try PythonRuntime.resolve()
        let root = runtime.root
        let python = runtime.executable
        let cache = try manager.url(for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let target = cache.appendingPathComponent("GigaAMLiquid/Media", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try manager.createDirectory(at: target, withIntermediateDirectories: true)
        var succeeded = false
        defer { if !succeeded { try? manager.removeItem(at: target) } }

        let task = Process()
        task.executableURL = python
        task.arguments = runtime.mediaDownloadArguments(url: url, target: target)
        task.currentDirectoryURL = root
        task.environment = runtime.environment
        task.standardInput = FileHandle.nullDevice
        let stdout = Pipe()
        let stderr = Pipe()
        task.standardOutput = stdout
        task.standardError = stderr
        let output = OutputTail(limit: 256 * 1024)
        let errors = OutputTail(limit: 64 * 1024)

        // Serialize launch with cancellation, so Cancel cannot miss a just-starting process.
        lock.lock()
        if cancelled { lock.unlock(); throw Failure.cancelled }
        do {
            try task.run()
            process = task
            lock.unlock()
        } catch {
            lock.unlock()
            throw Failure.message("\(L10n.text("Не удалось запустить Python для загрузки медиа."))\n\(python.path)\n\(error.localizedDescription)")
        }
        let readers = DispatchGroup()
        for (pipe, tail) in [(stdout, output), (stderr, errors)] {
            readers.enter()
            DispatchQueue.global(qos: .utility).async {
                tail.drain(pipe.fileHandleForReading)
                readers.leave()
            }
        }
        task.waitUntilExit()
        readers.wait()
        lock.lock()
        process = nil
        let wasCancelled = cancelled
        lock.unlock()
        if wasCancelled { throw Failure.cancelled }
        guard task.terminationReason == .exit, task.terminationStatus == 0 else {
            let diagnostics = String((errors.text.isEmpty ? output.text : errors.text).suffix(8192))
            let message = L10n.text("Не удалось загрузить медиа. Проверьте ссылку и Python-окружение проекта: нужны yt-dlp и зависимости requirements.txt.")
            throw Failure.message("\(message)\n\(L10n.text("Код завершения")): \(task.terminationStatus)\n\(diagnostics)")
        }
        // yt-dlp writes progress before app.py prints the final JSON response.
        guard let line = output.text.split(whereSeparator: { $0.isNewline }).last,
              let response = try? JSONDecoder().decode(DownloadResponse.self, from: Data(line.utf8)),
              !response.files.isEmpty else {
            throw Failure.message(L10n.text("Загрузчик не вернул список файлов. Проверьте Python-окружение проекта.") + "\n" + String(output.text.suffix(4096)))
        }
        let targetPath = target.resolvingSymlinksInPath().path + "/"
        var files: [URL] = []
        var seen = Set<URL>()
        for path in response.files {
            let file = URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath()
            guard path.hasPrefix("/"), file.path.hasPrefix(targetPath),
                  let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey]),
                  values.isRegularFile == true, (values.fileSize ?? 0) > 0,
                  manager.isReadableFile(atPath: file.path) else {
                throw Failure.message(L10n.text("Загрузчик вернул недоступный файл.") + "\n" + path)
            }
            if seen.insert(file).inserted { files.append(file) }
        }
        succeeded = true
        return (files, target)
    }
}
