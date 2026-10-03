import Foundation
import Testing
@testable import GigaAMLiquidCore

/// Same cases as tests/test_output_naming.py, plus the folder rule Liquid missed.
@Suite struct OutputNamingTests {
    private func url(_ path: String) -> URL { URL(fileURLWithPath: path) }

    @Test func fileNamesFollowTheWorkersTable() {
        #expect(OutputNaming.fileName(stem: "audio", format: "txt") == "audio.txt")
        #expect(OutputNaming.fileName(stem: "audio", format: "txt_timecodes") == "audio_timecodes.txt")
        #expect(OutputNaming.fileName(stem: "audio", format: "txt_diarize_timecodes") == "audio_diarize_timecodes.txt")
        #expect(OutputNaming.fileName(stem: "audio", format: "vtt") == "audio.vtt")
        #expect(OutputNaming.fileName(stem: "audio", format: "docx") == nil)
    }

    @Test func savedFileNamesMapBackToTheirFormat() {
        #expect(OutputNaming.format(ofOutputNamed: "talk_diarize.txt", stem: "talk") == "txt_diarize")
        #expect(OutputNaming.format(ofOutputNamed: "talk.md", stem: "talk") == "md")
        // An input whose own name ends in a suffix still maps by its full stem.
        #expect(OutputNaming.format(ofOutputNamed: "a_timecodes.txt", stem: "a_timecodes") == "txt")
        #expect(OutputNaming.format(ofOutputNamed: "other.txt", stem: "talk") == nil)
    }

    @Test func stemsMatchAcrossCaseAndUnicodeVariants() {
        #expect(OutputNaming.normalizedStem(url("/a/Café.wav")) == OutputNaming.normalizedStem(url("/b/CAFE\u{301}.mp3")))
        #expect(OutputNaming.normalizedStem(url("/a/Привет.wav")) == OutputNaming.normalizedStem(url("/b/ПРИВЕТ.mp3")))
        #expect(OutputNaming.normalizedStem(url("/a/Straße.wav")) == OutputNaming.normalizedStem(url("/b/STRASSE.mp3")))
    }

    @Test func sharedOutputFolderDetectsTheSameStem() {
        let files = [url("/tmp/a/same.wav"), url("/tmp/b/SAME.mp3")]
        #expect(OutputNaming.collisions(files, outputDirectory: url("/tmp/out")) == [files])
    }

    /// Liquid grouped by stem alone and refused /a/x.mp3 + /b/x.mp3 even when each
    /// result goes next to its own source, which the worker accepts.
    @Test func separateSourceFoldersDoNotCollideBesideTheSource() {
        let files = [url("/tmp/a/same.wav"), url("/tmp/b/same.mp3")]
        #expect(OutputNaming.collisions(files, outputDirectory: nil).isEmpty)
    }

    @Test func sameFolderCollidesBesideTheSource() {
        let files = [url("/tmp/a/talk.wav"), url("/tmp/b/talk.wav"), url("/tmp/a/TALK.mp4")]
        #expect(OutputNaming.collisions(files, outputDirectory: nil) == [[files[0], files[2]]])
    }
}
