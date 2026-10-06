import Foundation

/// The Тема setting: «Светлая» and «Тёмная» pin the look; «Системная» — also the
/// state before anything was chosen, which the popup shows as «Системная» —
/// follows macOS. It used to mean "light" whatever the system said.
public enum ThemeChoice {
    public static let light = "Светлая"
    public static let dark = "Тёмная"
    public static let system = "Системная"

    public static func followsSystem(_ setting: String?) -> Bool {
        setting != light && setting != dark
    }

    public static func isDark(setting: String?, systemIsDark: Bool) -> Bool {
        followsSystem(setting) ? systemIsDark : setting == dark
    }
}
