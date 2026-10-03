import CoreGraphics
import Foundation

/// Where a popup's chooser panel goes on screen: below its control if it fits,
/// otherwise above, otherwise on the roomier side, cut to the room there (its
/// list then scrolls). The panel used to be as tall as all its items, so a
/// 200-file batch in the Result page's file popup ran far off the screen.
public enum ChooserPlacement {
    public static func frame(anchor: CGRect, visible: CGRect, width: CGFloat, contentHeight: CGFloat,
                             gap: CGFloat = 6, maxHeight: CGFloat = 420, minHeight: CGFloat = 44) -> CGRect {
        let wanted = min(contentHeight, maxHeight)
        let below = anchor.minY - gap - visible.minY
        let above = visible.maxY - (anchor.maxY + gap)
        let height: CGFloat
        var y: CGFloat
        if wanted <= below {
            height = wanted
            y = anchor.minY - gap - height
        } else if wanted <= above {
            height = wanted
            y = anchor.maxY + gap
        } else if below >= above {
            height = max(min(below, wanted), min(minHeight, wanted))
            y = anchor.minY - gap - height
        } else {
            height = max(min(above, wanted), min(minHeight, wanted))
            y = anchor.maxY + gap
        }
        y = min(max(y, visible.minY), max(visible.minY, visible.maxY - height))
        let x = min(max(anchor.minX, visible.minX), max(visible.minX, visible.maxX - width))
        return CGRect(x: x, y: y, width: width, height: height)
    }
}
