import Foundation

/// Shared discovery for native front-ends; the Python project owns all media/model work.
struct PythonRuntime {
    let root: URL
    let executable: URL
    let environment: [String: String]
    let frozenCompanion: Bool
    /// Where worker processes run. The source tree for development; a writable
    /// Application Support folder for a frozen companion, which may live inside the
    /// signed GigaAMLiquid.app bundle where nothing (e.g. processing_stats.json)
    /// may be written.
    let workingDirectory: URL

    var transcriptionArguments: [String] {
        frozenCompanion ? ["--native-worker"] : ["-m", "src.tui_worker"]
    }

    func mediaDownloadArguments(url: URL, target: URL) -> [String] {
        let arguments = ["--media-download-smoke", url.absoluteString, target.path]
        return frozenCompanion ? arguments : [root.appendingPathComponent("app.py").path] + arguments
    }

    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static func resolve(environment: [String: String] = ProcessInfo.processInfo.environment) throws -> PythonRuntime {
        let root = try projectRoot(environment: environment)
        let companion = companionURL(root: root, environment: environment)
        let executable = try companion ?? pythonURL(root: root, environment: environment)
        var childEnvironment = environment
        childEnvironment["PYTHONUNBUFFERED"] = "1"
        childEnvironment["PYTHONIOENCODING"] = "utf-8"
        if companion != nil && hasBundledModels(root: root) {
            childEnvironment["HF_HUB_OFFLINE"] = "1"
            childEnvironment["TRANSFORMERS_OFFLINE"] = "1"
        }
        return PythonRuntime(
            root: root,
            executable: executable,
            environment: childEnvironment,
            frozenCompanion: companion != nil,
            workingDirectory: companion != nil ? try supportDirectory() : root
        )
    }

    private static func supportDirectory() throws -> URL {
        let manager = FileManager.default
        let base = try manager.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let directory = base.appendingPathComponent("GigaAMLiquid", isDirectory: true)
        try manager.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    private static func companionURL(root: URL, environment: [String: String]) -> URL? {
        guard environment["GIGAAM_PYTHON", default: ""].isEmpty else { return nil }
        let candidate = root.appendingPathComponent(
            "GigaAMTranscriber.app/Contents/MacOS/GigaAMTranscriber"
        )
        return FileManager.default.isExecutableFile(atPath: candidate.path) ? candidate : nil
    }

    private static func hasBundledModels(root: URL) -> Bool {
        let models = root.appendingPathComponent("models/hf", isDirectory: true)
        guard let contents = try? FileManager.default.contentsOfDirectory(
            at: models,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ) else { return false }
        return !contents.isEmpty
    }

    private static func projectRoot(environment: [String: String]) throws -> URL {
        let manager = FileManager.default
        func isPreparedRoot(_ url: URL) -> Bool {
            let hasSourceRuntime = ["app.py", "src/utils/media_downloader.py"].allSatisfy {
                let file = url.appendingPathComponent($0)
                return (try? file.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true
            }
            let companion = url.appendingPathComponent(
                "GigaAMTranscriber.app/Contents/MacOS/GigaAMTranscriber"
            )
            return hasSourceRuntime || manager.isExecutableFile(atPath: companion.path)
        }
        if let override = environment["GIGAAM_PROJECT_ROOT"], !override.isEmpty {
            let root = URL(fileURLWithPath: (override as NSString).expandingTildeInPath).standardizedFileURL
            if isPreparedRoot(root) { return root }
            throw Failure(message: L10n.text("GIGAAM_PROJECT_ROOT должен указывать на папку с GigaAMTranscriber.app или подготовленным Python-проектом."))
        }
        // A self-contained release keeps the companion (and, offline, models/hf) in
        // the app's own Contents/Resources; older archives put it beside the app.
        if let resources = Bundle.main.resourceURL?.standardizedFileURL, isPreparedRoot(resources) { return resources }
        let starts = [Bundle.main.executableURL?.deletingLastPathComponent(), URL(fileURLWithPath: manager.currentDirectoryPath)].compactMap { $0 }
        for start in starts {
            var candidate = start.resolvingSymlinksInPath().standardizedFileURL
            while true {
                if isPreparedRoot(candidate) { return candidate }
                let parent = candidate.deletingLastPathComponent().standardizedFileURL
                if parent.path == candidate.path { break }
                candidate = parent
            }
        }
        throw Failure(message: L10n.text("Не найден GigaAMTranscriber.app или Python runtime. Не перемещайте приложения из папки релиза либо задайте GIGAAM_PROJECT_ROOT."))
    }

    private static func pythonURL(root: URL, environment: [String: String]) throws -> URL {
        let manager = FileManager.default
        func executable(_ name: String) -> URL? {
            let expanded = (name as NSString).expandingTildeInPath
            let candidates: [URL]
            if expanded.contains("/") {
                candidates = [URL(fileURLWithPath: expanded, relativeTo: root).standardizedFileURL]
            } else {
                candidates = (environment["PATH"] ?? "/usr/bin:/bin").split(separator: ":").map {
                    URL(fileURLWithPath: String($0), isDirectory: true).appendingPathComponent(expanded)
                }
            }
            return candidates.first { manager.isExecutableFile(atPath: $0.path) }
        }
        if let override = environment["GIGAAM_PYTHON"], !override.isEmpty {
            guard let python = executable(override) else {
                throw Failure(message: L10n.text("GIGAAM_PYTHON должен указывать на исполняемый Python с зависимостями проекта."))
            }
            return python
        }
        var candidates = [root.appendingPathComponent(".venv/bin/python").path, root.appendingPathComponent("venv/bin/python").path]
        if let virtualEnv = environment["VIRTUAL_ENV"] { candidates.append(virtualEnv + "/bin/python") }
        candidates.append(contentsOf: ["python3", "python"])
        for candidate in candidates {
            if let python = executable(candidate) { return python }
        }
        throw Failure(message: L10n.text("Python не найден. Подготовьте .venv с зависимостями проекта или задайте GIGAAM_PYTHON. Автоматическая установка не выполняется."))
    }
}
