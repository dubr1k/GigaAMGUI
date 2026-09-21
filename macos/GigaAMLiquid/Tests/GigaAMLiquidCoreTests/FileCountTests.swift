import Testing
@testable import GigaAMLiquidCore

@Suite struct FileCountTests {
    @Test func russianPluralForms() {
        #expect(FileCount.text(1, english: false) == "1 файл")
        #expect(FileCount.text(2, english: false) == "2 файла")
        #expect(FileCount.text(4, english: false) == "4 файла")
        #expect(FileCount.text(5, english: false) == "5 файлов")
        #expect(FileCount.text(11, english: false) == "11 файлов")
        #expect(FileCount.text(21, english: false) == "21 файл")
        #expect(FileCount.text(112, english: false) == "112 файлов")
    }

    @Test func englishPluralForms() {
        #expect(FileCount.text(1, english: true) == "1 file")
        #expect(FileCount.text(2, english: true) == "2 files")
    }
}
