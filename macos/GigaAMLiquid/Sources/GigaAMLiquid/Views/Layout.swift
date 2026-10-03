import AppKit

final class AutoLayoutDocumentView: NSView {
    override var isFlipped: Bool { true }
}

final class EditorScrollView: NSScrollView {
    /// A wrapping text view must be exactly as wide as the visible area. Autoresizing
    /// only adds the clip view's size delta to the text view's initial frame width,
    /// which left editors ~240 pt wider than their box and cut long lines off.
    override func tile() {
        super.tile()
        if let text = documentView as? NSTextView, !text.isHorizontallyResizable,
           text.frame.width != contentSize.width {
            text.setFrameSize(NSSize(width: contentSize.width, height: text.frame.height))
        }
    }

    override func scrollWheel(with event: NSEvent) {
        if let outer = superview?.enclosingScrollView, let documentView {
            let visible = contentView.bounds
            let atStart = visible.minY <= 0.5
            let atEnd = visible.maxY >= documentView.bounds.height - 0.5
            if documentView.bounds.height <= visible.height + 0.5 ||
                (event.scrollingDeltaY > 0 && atStart) ||
                (event.scrollingDeltaY < 0 && atEnd) {
                outer.scrollWheel(with: event)
                return
            }
        }
        super.scrollWheel(with: event)
    }
}

final class ContentStackView: NSStackView {
    var fillsWidth = true
    override func addArrangedSubview(_ view: NSView) {
        view.translatesAutoresizingMaskIntoConstraints = false
        super.addArrangedSubview(view)
        if orientation == .vertical && fillsWidth {
            view.widthAnchor.constraint(equalTo: widthAnchor).isActive = true
        }
    }
}

final class DropZoneView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        let rect = bounds.insetBy(dx: 0.5, dy: 0.5)
        let path = NSBezierPath(roundedRect: rect, xRadius: 14, yRadius: 14)
        let dash: [CGFloat] = [6, 6]
        path.setLineDash(dash, count: dash.count, phase: 0)
        Palette.line.setStroke()
        path.lineWidth = 1
        path.stroke()
    }
}

final class ProgressTrackView: NSView {
    var fraction: Double = 0 { didSet { needsDisplay = true } }

    override func draw(_ dirtyRect: NSRect) {
        let track = NSBezierPath(roundedRect: bounds, xRadius: 4, yRadius: 4)
        (Palette.isDark ? NSColor(calibratedWhite: 0.24, alpha: 1) : NSColor(calibratedWhite: 0.48, alpha: 1)).setFill()
        track.fill()
        Palette.ink.setFill()
        NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: bounds.width * min(1, max(0, fraction)), height: bounds.height), xRadius: 4, yRadius: 4).fill()
    }
}
