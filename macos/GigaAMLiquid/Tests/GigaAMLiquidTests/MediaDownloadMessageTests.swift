import Testing
@testable import GigaAMLiquid

/// The bundled worker has no project Python environment: telling a user of the
/// installed app to check requirements.txt sent them after the wrong problem when
/// YouTube rejected the stale yt-dlp with HTTP 403.
@Suite struct MediaDownloadMessageTests {
    @Test func bundledWorkerFailureDoesNotBlameThePythonEnvironment() {
        let message = MediaDownloadJob.failureMessage(frozenCompanion: true)
        #expect(!message.contains("requirements.txt"))
        #expect(!message.contains("Python"))
    }

    @Test func sourceRunFailureStillPointsAtThePythonEnvironment() {
        #expect(MediaDownloadJob.failureMessage(frozenCompanion: false).contains("requirements.txt"))
    }
}
