import Foundation

/// Shared discovery for native front-ends; the Python project owns all media/model work.
struct PythonRuntime {
    let root: URL
    let executable: URL
    let environment: [String: String]

    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static func resolve(environment: [String: String] = ProcessInfo.processInfo.environment) throws -> PythonRuntime {
        let root = try projectRoot(environment: environment)
        let executable = try pythonURL(root: root, environment: environment)
        var childEnvironment = environment
        childEnvironment["PYTHONUNBUFFERED"] = "1"
        childEnvironment["PYTHONIOENCODING"] = "utf-8"
        return PythonRuntime(root: root, executable: executable, environment: childEnvironment)
    }

    private static func projectRoot(environment: [String: String]) throws -> URL {
        let manager = FileManager.default
        func isProject(_ url: URL) -> Bool {
            ["app.py", "src/utils/media_downloader.py"].allSatisfy {
                let file = url.appendingPathComponent($0)
                return (try? file.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true
            }
        }
        if let override = environment["GIGAAM_PROJECT_ROOT"], !override.isEmpty {
            let root = URL(fileURLWithPath: (override as NSString).expandingTildeInPath).standardizedFileURL
            if isProject(root) { return root }
            throw Failure(message: L10n.text("GIGAAM_PROJECT_ROOT должен указывать на папку проекта с app.py и src/utils/media_downloader.py."))
        }
        let starts = [Bundle.main.executableURL?.deletingLastPathComponent(), URL(fileURLWithPath: manager.currentDirectoryPath)].compactMap { $0 }
        for start in starts {
            var candidate = start.resolvingSymlinksInPath().standardizedFileURL
            while true {
                if isProject(candidate) { return candidate }
                let parent = candidate.deletingLastPathComponent().standardizedFileURL
                if parent.path == candidate.path { break }
                candidate = parent
            }
        }
        throw Failure(message: L10n.text("Не найдены файлы загрузчика. Запустите клиент из папки проекта или задайте GIGAAM_PROJECT_ROOT."))
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
