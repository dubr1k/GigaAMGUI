import AppKit
import Testing
@testable import GigaAMLiquid

/// The renderer must show exactly what a full re-render would — finals, then the
/// drafts — while touching only the changed part of the text storage.
@Suite struct LiveTranscriptRendererTests {
    private let placeholder = "Нет фрагментов."

    private final class EditLog: NSObject, NSTextStorageDelegate {
        var edits: [NSRange] = []
        func textStorage(_ textStorage: NSTextStorage, didProcessEditing editedMask: NSTextStorageEditActions,
                         range editedRange: NSRange, changeInLength delta: Int) {
            if editedMask.contains(.editedCharacters) { edits.append(editedRange) }
        }
    }

    private func expected(_ finals: [String], _ drafts: [String]) -> String {
        finals.isEmpty && drafts.isEmpty ? placeholder : (finals + drafts).joined(separator: "\n")
    }

    @Test func matchesAFullRenderThroughASession() {
        let renderer = LiveTranscriptRenderer()
        let storage = NSTextStorage()
        var finals: [String] = []
        var revision = 0
        func check(_ drafts: [String]) {
            renderer.render(finals: finals, revision: revision, drafts: drafts, placeholder: placeholder, into: storage, attributes: [:])
            #expect(storage.string == expected(finals, drafts))
        }
        check([])
        check(["[mic …] Привет"])
        check(["[mic …] Привет, как"])
        finals.append("Привет, как дела?")
        check([])
        check(["[mic …] Хорошо", "[system …] Музыка"])
        finals.append("Хорошо.")
        check(["[system …] Музыка"])
        finals[0] = "Привет! Как дела?"   // a shown final was revised
        revision += 1
        check([])
        finals = []
        revision += 1
        check([])
    }

    @Test func aNewDraftOnlyTouchesTheTail() {
        let renderer = LiveTranscriptRenderer()
        let storage = NSTextStorage()
        let finals = (0..<200).map { "Фраза номер \($0)." }
        renderer.render(finals: finals, revision: 0, drafts: [], placeholder: placeholder, into: storage, attributes: [:])
        let finalsLength = storage.length
        let log = EditLog()
        storage.delegate = log
        renderer.render(finals: finals, revision: 0, drafts: ["[mic …] новая"], placeholder: placeholder, into: storage, attributes: [:])
        #expect(log.edits.allSatisfy { $0.location >= finalsLength })
        #expect(storage.string.hasSuffix("Фраза номер 199.\n[mic …] новая"))
    }

    @Test func resetAfterTheViewIsRebuiltRendersEverything() {
        let renderer = LiveTranscriptRenderer()
        renderer.render(finals: ["Один."], revision: 0, drafts: [], placeholder: placeholder, into: NSTextStorage(), attributes: [:])
        renderer.reset()
        let fresh = NSTextStorage()
        renderer.render(finals: ["Один.", "Два."], revision: 0, drafts: [], placeholder: placeholder, into: fresh, attributes: [:])
        #expect(fresh.string == "Один.\nДва.")
    }
}
