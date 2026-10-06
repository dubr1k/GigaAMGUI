import Foundation
import Testing
@testable import GigaAMLiquidCore

@Suite struct MediaScanTests {
    private func makeTree() throws -> URL {
        let root = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("MediaScanTests-\(UUID().uuidString)")
        let manager = FileManager.default
        try manager.createDirectory(at: root.appendingPathComponent("nested/deeper"), withIntermediateDirectories: true)
        try manager.createDirectory(at: root.appendingPathComponent(".hidden"), withIntermediateDirectories: true)
        for name in ["b.mp3", "a.WAV", "notes.txt", "nested/clip.m4a", "nested/deeper/talk.mkv", "nested/deeper/readme.md", ".hidden/secret.mp3"] {
            try Data().write(to: root.appendingPathComponent(name))
        }
        return root
    }

    @Test func foldersAreWalkedRecursivelyLikeThePyQtClient() throws {
        let root = try makeTree()
        defer { try? FileManager.default.removeItem(at: root) }

        let names = MediaScan.expand([root]).map { $0.path.replacingOccurrences(of: root.resolvingSymlinksInPath().path + "/", with: "") }

        #expect(names == ["a.WAV", "b.mp3", "nested/clip.m4a", "nested/deeper/talk.mkv"])
    }

    @Test func filesKeepTheirOrderAndDuplicatesCollapse() throws {
        let root = try makeTree()
        defer { try? FileManager.default.removeItem(at: root) }
        let clip = root.appendingPathComponent("nested/clip.m4a")

        let result = MediaScan.expand([clip, root.appendingPathComponent("notes.txt"), root, clip])

        #expect(result.first == clip.resolvingSymlinksInPath())
        #expect(result.count == 4)
        #expect(result.filter { $0.lastPathComponent == "clip.m4a" }.count == 1)
    }

    @Test func missingAndUnsupportedItemsAreIgnored() {
        #expect(MediaScan.expand([URL(fileURLWithPath: "/nonexistent/x.mp3"), URL(fileURLWithPath: "/etc/hosts")]).isEmpty)
    }

    @Test func extensionListMatchesConfigPy() {
        #expect(MediaScan.isMedia(URL(fileURLWithPath: "/x/a.QTA")))
        #expect(MediaScan.isMedia(URL(fileURLWithPath: "/x/a.3gp")))
        #expect(!MediaScan.isMedia(URL(fileURLWithPath: "/x/a.srt")))
    }
}
