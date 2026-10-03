import AppKit

/// Light/dark colours of the Liquid look, chosen by the Тема setting.
enum Palette {
    static var isDark: Bool { UserDefaults.standard.string(forKey: "settings.theme") == "Тёмная" }
    static var ink: NSColor { isDark ? NSColor(calibratedWhite: 0.94, alpha: 1) : NSColor(calibratedWhite: 0.05, alpha: 1) }
    static var body: NSColor { isDark ? NSColor(calibratedWhite: 0.72, alpha: 1) : NSColor(calibratedRed: 0.22, green: 0.27, blue: 0.33, alpha: 1) }
    static var muted: NSColor { isDark ? NSColor(calibratedWhite: 0.50, alpha: 1) : NSColor(calibratedRed: 0.34, green: 0.38, blue: 0.44, alpha: 1) }
    static var blue: NSColor { isDark ? .white : .black }
    static var line: NSColor { isDark ? NSColor(calibratedWhite: 0.20, alpha: 1) : NSColor(calibratedRed: 0.42, green: 0.46, blue: 0.52, alpha: 1) }
    static var selection: NSColor { isDark ? NSColor(calibratedWhite: 0.16, alpha: 0.98) : NSColor(calibratedWhite: 0.86, alpha: 0.96) }
    static var primary: NSColor { isDark ? NSColor(calibratedWhite: 0.94, alpha: 1) : ink }

    static func fieldBackground(enabled: Bool) -> NSColor {
        NSColor.white.withAlphaComponent(isDark ? (enabled ? 0.16 : 0.04) : (enabled ? 0.52 : 0.20))
    }
}
