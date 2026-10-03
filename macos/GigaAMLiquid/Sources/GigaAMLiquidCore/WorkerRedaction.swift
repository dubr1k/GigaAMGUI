import Foundation

/// Redacts secrets and credential-looking substrings from worker output before it
/// reaches logs or error messages, and caps the result to the last 8 KiB.
///
/// For diagnostics only: user content (an assistant's answer, a transcript) must
/// not go through it — the patterns rewrite ordinary prose such as "Bearer token".
public enum WorkerRedaction {
    private static let credentialPatterns = [
        #"\b(?:hf_|sk-)[A-Za-z0-9_-]+"#,
        #"(?i)\bBearer\s+\S+"#,
        #"(?i)((?:token|api[_-]?key|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+"#,
        #"(?i)(https?://)[^\s/@]+:[^\s/@]+@"#
    ].compactMap { try? NSRegularExpression(pattern: $0) }

    /// Longest tail of a diagnostic that is kept.
    public static let limit = 8192

    public static func safeText(_ text: String, secrets: [String]) -> String {
        var value = text
        for secret in secrets where !secret.isEmpty { value = value.replacingOccurrences(of: secret, with: "[redacted]") }
        for pattern in credentialPatterns {
            value = pattern.stringByReplacingMatches(in: value, range: NSRange(value.startIndex..., in: value), withTemplate: "[redacted]")
        }
        return String(value.suffix(limit))
    }

    /// Environment values that look like credentials, for `safeText(_:secrets:)`.
    public static func secrets(in environment: [String: String]) -> [String] {
        environment.compactMap { key, value in
            let name = key.uppercased()
            return value.count >= 6 && ["TOKEN", "SECRET", "PASSWORD", "API_KEY"].contains(where: name.contains) ? value : nil
        }
    }
}
