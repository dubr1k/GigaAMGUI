import Testing
@testable import GigaAMLiquidCore

@Suite struct ThemeChoiceTests {
    @Test func systemThemeFollowsMacOS() {
        #expect(ThemeChoice.isDark(setting: "Системная", systemIsDark: true))
        #expect(!ThemeChoice.isDark(setting: "Системная", systemIsDark: false))
        // Nothing chosen yet: the popup shows «Системная», so behave like it.
        #expect(ThemeChoice.isDark(setting: nil, systemIsDark: true))
    }

    @Test func explicitThemesIgnoreTheSystem() {
        #expect(ThemeChoice.isDark(setting: "Тёмная", systemIsDark: false))
        #expect(!ThemeChoice.isDark(setting: "Светлая", systemIsDark: true))
        #expect(!ThemeChoice.followsSystem("Тёмная") && ThemeChoice.followsSystem("Системная"))
    }
}
