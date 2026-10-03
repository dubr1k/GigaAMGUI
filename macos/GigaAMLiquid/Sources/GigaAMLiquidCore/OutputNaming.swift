import Foundation

/// Result file names, mirroring src/utils/output_naming.py (the worker writes the
/// files; the client must find them and refuse batches that would overwrite each
/// other the same way the worker would).
public enum OutputNaming {
    /// FORMAT_SUFFIX: format key → (name suffix, extension). Checked against Python by pytest.
    public static let formatSuffix: [String: (suffix: String, ext: String)] = [
        "txt": ("", "txt"),
        "txt_timecodes": ("_timecodes", "txt"),
        "txt_diarize": ("_diarize", "txt"),
        "txt_diarize_timecodes": ("_diarize_timecodes", "txt"),
        "md": ("", "md"),
        "srt": ("", "srt"),
        "vtt": ("", "vtt"),
    ]

    /// output_filename(stem, fmt), or nil for an unknown format.
    public static func fileName(stem: String, format: String) -> String? {
        formatSuffix[format].map { "\(stem)\($0.suffix).\($0.ext)" }
    }

    /// The format whose result for an input called `stem` is named `name`.
    public static func format(ofOutputNamed name: String, stem: String) -> String? {
        formatSuffix.keys.sorted().first { fileName(stem: stem, format: $0) == name }
    }

    /// normalized_output_stem: compatibility-decomposed, combining marks dropped,
    /// recomposed and case-folded, so "Café" and "CAFE\u{301}" are one name.
    public static func normalizedStem(_ url: URL) -> String {
        let stem = url.deletingPathExtension().lastPathComponent
        let decomposed = stem.decomposedStringWithCompatibilityMapping.unicodeScalars
            .filter { $0.properties.canonicalCombiningClass.rawValue == 0 }
        return String(String.UnicodeScalarView(decomposed)).precomposedStringWithCompatibilityMapping
            .folding(options: .caseInsensitive, locale: nil)
    }

    /// find_output_collisions: groups of inputs that would write the same result
    /// names. Inputs collide only within one target folder — the shared output
    /// folder, or each input's own folder when results go next to the source.
    public static func collisions(_ inputs: [URL], outputDirectory: URL?) -> [[URL]] {
        let shared = outputDirectory?.standardizedFileURL.path
        var order: [String] = []
        var groups: [String: [URL]] = [:]
        for input in inputs {
            let directory = shared ?? input.standardizedFileURL.deletingLastPathComponent().path
            let key = directory + "\u{0}" + normalizedStem(input)
            if groups[key] == nil { order.append(key) }
            groups[key, default: []].append(input)
        }
        return order.compactMap { groups[$0] }.filter { $0.count > 1 }
    }
}
