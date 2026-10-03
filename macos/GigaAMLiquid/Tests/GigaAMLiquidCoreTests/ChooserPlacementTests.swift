import CoreGraphics
import Foundation
import Testing
@testable import GigaAMLiquidCore

@Suite struct ChooserPlacementTests {
    private let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)

    @Test func shortListOpensBelowItsControl() {
        let anchor = CGRect(x: 100, y: 600, width: 200, height: 28)
        let frame = ChooserPlacement.frame(anchor: anchor, visible: screen, width: 200, contentHeight: 140)
        #expect(frame == CGRect(x: 100, y: 454, width: 200, height: 140))
    }

    @Test func listThatDoesNotFitBelowOpensAbove() {
        let anchor = CGRect(x: 100, y: 100, width: 200, height: 28)
        let frame = ChooserPlacement.frame(anchor: anchor, visible: screen, width: 200, contentHeight: 300)
        #expect(frame.minY == 134 && frame.height == 300)
    }

    /// 200 files: 6412 pt of rows. The panel stays on screen and scrolls.
    @Test func longListIsCappedAndStaysOnScreen() {
        let anchor = CGRect(x: 100, y: 300, width: 200, height: 28)
        let frame = ChooserPlacement.frame(anchor: anchor, visible: screen, width: 200, contentHeight: 200 * 32 + 12)
        #expect(frame.height == 420)
        #expect(screen.contains(frame))
    }

    @Test func crampedControlStillGetsAUsablePanelInsideTheScreen() {
        let anchor = CGRect(x: 1400, y: 10, width: 200, height: 28)
        let frame = ChooserPlacement.frame(anchor: anchor, visible: screen, width: 200, contentHeight: 2000)
        #expect(screen.contains(frame))
        #expect(frame.height >= 44)
        #expect(frame.maxX <= screen.maxX)
    }
}
