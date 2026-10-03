import AppKit

final class NavigationRowButton: NSButton {
    var keyHandler: ((NSEvent) -> Bool)?
    var acceptsInitialClick = false

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        acceptsInitialClick || super.acceptsFirstMouse(for: event)
    }

    override func keyDown(with event: NSEvent) {
        if keyHandler?(event) != true { super.keyDown(with: event) }
    }

    override func draw(_ dirtyRect: NSRect) {
        let color = contentTintColor ?? .labelColor
        var textX: CGFloat = 14
        if let image {
            let symbol = image.withSymbolConfiguration(.init(paletteColors: [color])) ?? image
            symbol.draw(in: NSRect(x: 14, y: (bounds.height - 18) / 2, width: 18, height: 18))
            textX = 42
        }
        let text = NSAttributedString(string: title, attributes: [
            .font: font ?? NSFont.systemFont(ofSize: 14),
            .foregroundColor: color
        ])
        text.draw(in: NSRect(x: textX, y: (bounds.height - text.size().height) / 2,
                            width: bounds.width - textX - 14, height: text.size().height))
    }
}

final class PaddedButton: NSButton {
    var keyHandler: ((NSEvent) -> Bool)?

    // Layer-backed buttons keep their filled background when disabled; AppKit only
    // greys the title, which is invisible on the black primary button.
    override var isEnabled: Bool {
        didSet { alphaValue = isEnabled ? 1 : 0.45 }
    }

    override func keyDown(with event: NSEvent) {
        if keyHandler?(event) != true { super.keyDown(with: event) }
    }

    override var intrinsicContentSize: NSSize {
        let natural = super.intrinsicContentSize
        return NSSize(width: natural.width + 28, height: natural.height)
    }

    override func draw(_ dirtyRect: NSRect) {
        guard alignment == .left else { super.draw(dirtyRect); return }
        let arrowWidth: CGFloat = imagePosition == .imageTrailing && image != nil ? 24 : 0
        let text = NSMutableAttributedString(attributedString: attributedTitle)
        if !isEnabled { text.addAttribute(.foregroundColor, value: Palette.muted, range: NSRange(location: 0, length: text.length)) }
        let width = max(0, bounds.width - 28 - arrowWidth)
        let height = min(bounds.height - 12, ceil(text.boundingRect(with: NSSize(width: width, height: .greatestFiniteMagnitude), options: [.usesLineFragmentOrigin, .usesFontLeading]).height))
        text.draw(with: NSRect(x: 14, y: (bounds.height - height) / 2, width: width, height: height), options: [.usesLineFragmentOrigin, .usesFontLeading])
        if arrowWidth > 0 {
            let color = isEnabled ? (contentTintColor ?? Palette.body) : Palette.muted
            let arrow = image?.withSymbolConfiguration(.init(paletteColors: [color]))
            arrow?.draw(in: NSRect(x: bounds.width - 26, y: (bounds.height - 12) / 2, width: 12, height: 12))
        }
    }
}

final class RoundedCheckButton: NSButton {
    override func draw(_ dirtyRect: NSRect) {
        let rect = NSRect(x: 0.5, y: (bounds.height - 16) / 2, width: 16, height: 16)
        let shape = NSBezierPath(roundedRect: rect, xRadius: 6, yRadius: 6)
        (state == .on ? Palette.blue : (Palette.isDark ? NSColor.white.withAlphaComponent(0.12) : NSColor.black.withAlphaComponent(0.10))).setFill()
        shape.fill()
        if state != .on && !Palette.isDark {
            Palette.line.setStroke()
            shape.lineWidth = 1
            shape.stroke()
        }
        let text = NSAttributedString(string: title, attributes: [
            .font: font ?? NSFont.systemFont(ofSize: 12),
            .foregroundColor: isEnabled ? Palette.body : Palette.muted
        ])
        text.draw(in: NSRect(x: 24, y: (bounds.height - text.size().height) / 2,
                            width: max(0, bounds.width - 24), height: text.size().height))
    }
}

final class ThemedSwitch: NSButton {
    override var state: NSControl.StateValue {
        didSet { needsDisplay = true }
    }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setButtonType(.switch)
        title = ""
        isBordered = false
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override var intrinsicContentSize: NSSize { NSSize(width: 36, height: 22) }

    override func draw(_ dirtyRect: NSRect) {
        let height: CGFloat = 18
        let width: CGFloat = 32
        let track = NSRect(x: bounds.midX - width / 2, y: bounds.midY - height / 2, width: width, height: height)
        (state == .on ? Palette.blue : NSColor(calibratedWhite: 0.62, alpha: 1)).setFill()
        NSBezierPath(roundedRect: track, xRadius: height / 2, yRadius: height / 2).fill()
        let knob = NSRect(x: state == .on ? track.maxX - 16 : track.minX + 2, y: track.minY + 2, width: 14, height: 14)
        (state == .on && Palette.isDark ? NSColor.black : NSColor.white).setFill()
        NSBezierPath(ovalIn: knob).fill()
    }
}

final class CircularIconButton: NSButton {
    override func draw(_ dirtyRect: NSRect) {
        let diameter = min(bounds.width, bounds.height)
        let circleRect = NSRect(x: bounds.midX - diameter / 2, y: bounds.midY - diameter / 2,
                                width: diameter, height: diameter).insetBy(dx: 0.5, dy: 0.5)
        NSColor.white.withAlphaComponent(Palette.isDark ? 0.07 : 0.5).setFill()
        NSBezierPath(ovalIn: circleRect).fill()
        let symbol = image?.withSymbolConfiguration(.init(paletteColors: [Palette.ink]))
        symbol?.draw(in: NSRect(x: bounds.midX - 9, y: bounds.midY - 9, width: 18, height: 18))
    }
}
