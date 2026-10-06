import Foundation

/// «N файлов» для заголовка списка выбранных файлов, с русскими формами
/// множественного числа (1 файл, 2–4 файла, 5–20 файлов, 21 файл, 112 файлов).
public enum FileCount {
    public static func text(_ count: Int, english: Bool) -> String {
        if english {
            return "\(count) \(count == 1 ? "file" : "files")"
        }
        let lastTwo = count % 100
        let last = count % 10
        let word: String
        if (11...14).contains(lastTwo) {
            word = "файлов"
        } else if last == 1 {
            word = "файл"
        } else if (2...4).contains(last) {
            word = "файла"
        } else {
            word = "файлов"
        }
        return "\(count) \(word)"
    }
}
