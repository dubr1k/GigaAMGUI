import AppKit
import Testing
@testable import GigaAMLiquid

/// AppController keeps the main window in a strong property and still talks to it
/// after the user closes it: closing the last window terminates the app, and
/// applicationShouldTerminate ends editing on that window. A window AppKit releases
/// on close leaves that property dangling — 2.6.0 crashed on every close button.
@Suite struct WindowLifetimeTests {
    @MainActor @Test func mainWindowOutlivesBeingClosed() throws {
        let window = ApplicationWindow(contentRect: NSRect(x: 0, y: 0, width: 320, height: 240),
                                       styleMask: [.titled, .closable], backing: .buffered, defer: false)
        // Checked first: with the default the close below over-releases the window
        // and the test process dies instead of reporting a failure.
        try #require(!window.isReleasedWhenClosed)
        weak var observed = window
        autoreleasepool { window.close() }
        #expect(observed != nil)
        #expect(window.makeFirstResponder(nil))
    }
}
