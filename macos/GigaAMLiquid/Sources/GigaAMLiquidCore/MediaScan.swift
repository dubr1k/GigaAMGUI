import Foundation

/// Expands dropped items into media files the way the PyQt client does
/// (`files_mixin._collect_supported_open_paths`): folders are walked recursively,
/// files are kept by extension, duplicates are dropped, order is stable.
public enum MediaScan {
    /// Mirrors `MEDIA_EXTENSIONS` in src/config.py.
    public static let mediaExtensions: Set<String> = [
        "mp3", "wav", "m4a", "aac", "mp4", "avi", "mov", "mkv", "webm",
        "flac", "ogg", "wma", "qta", "3gp"
    ]

    public static func isMedia(_ url: URL) -> Bool {
        mediaExtensions.contains(url.pathExtension.lowercased())
    }

    /// Media files among `urls`; a folder contributes every media file beneath it
    /// (hidden entries skipped, sorted by path). Symlinks are resolved so the same
    /// file reached twice counts once.
    public static func expand(_ urls: [URL], manager: FileManager = .default) -> [URL] {
        var seen = Set<URL>()
        var result: [URL] = []
        func append(_ url: URL) {
            let resolved = url.standardizedFileURL.resolvingSymlinksInPath()
            if isMedia(resolved), seen.insert(resolved).inserted { result.append(resolved) }
        }
        for url in urls {
            var isDirectory: ObjCBool = false
            guard manager.fileExists(atPath: url.path, isDirectory: &isDirectory) else { continue }
            guard isDirectory.boolValue else { append(url); continue }
            guard let enumerator = manager.enumerator(
                at: url, includingPropertiesForKeys: [.isRegularFileKey], options: [.skipsHiddenFiles]
            ) else { continue }
            var found: [URL] = []
            for case let entry as URL in enumerator
            where (try? entry.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true {
                found.append(entry)
            }
            for entry in found.sorted(by: { $0.path < $1.path }) { append(entry) }
        }
        return result
    }
}
