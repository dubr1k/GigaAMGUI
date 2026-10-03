import AppKit

final class CenteredTextCell: NSTextFieldCell {
    override func drawingRect(forBounds rect: NSRect) -> NSRect {
        let height = min(rect.height, ceil((font?.ascender ?? 13) - (font?.descender ?? -3) + (font?.leading ?? 0)))
        return NSRect(x: rect.minX + 12, y: rect.midY - height / 2, width: max(0, rect.width - 24), height: height)
    }

    override func select(withFrame rect: NSRect, in view: NSView, editor: NSText, delegate: Any?, start: Int, length: Int) {
        super.select(withFrame: drawingRect(forBounds: rect), in: view, editor: editor, delegate: delegate, start: start, length: length)
    }

    override func edit(withFrame rect: NSRect, in view: NSView, editor: NSText, delegate: Any?, event: NSEvent?) {
        super.edit(withFrame: drawingRect(forBounds: rect), in: view, editor: editor, delegate: delegate, event: event)
    }
}

final class RoundedTextField: NSTextField {
    /// One radius for the background and the focus ring: a ring drawn with a
    /// different radius than the field pokes out at the corners.
    var cornerRadius: CGFloat = 10

    override var isEnabled: Bool {
        didSet { needsLayout = true; needsDisplay = true }
    }

    override var focusRingMaskBounds: NSRect { bounds }

    override func drawFocusRingMask() {
        NSBezierPath(roundedRect: bounds, xRadius: cornerRadius, yRadius: cornerRadius).fill()
    }

    override func layout() {
        super.layout()
        layer?.cornerRadius = cornerRadius
        layer?.cornerCurve = .continuous
        layer?.backgroundColor = Palette.fieldBackground(enabled: isEnabled).cgColor
        textColor = isEnabled ? Palette.ink : Palette.muted
        noteFocusRingMaskChanged()
    }
}

final class CenteredSecureTextCell: NSSecureTextFieldCell {
    override func drawingRect(forBounds rect: NSRect) -> NSRect {
        let height = min(rect.height, ceil((font?.ascender ?? 13) - (font?.descender ?? -3) + (font?.leading ?? 0)))
        return NSRect(x: rect.minX + 12, y: rect.midY - height / 2, width: max(0, rect.width - 24), height: height)
    }

    override func select(withFrame rect: NSRect, in view: NSView, editor: NSText, delegate: Any?, start: Int, length: Int) {
        super.select(withFrame: drawingRect(forBounds: rect), in: view, editor: editor, delegate: delegate, start: start, length: length)
    }

    override func edit(withFrame rect: NSRect, in view: NSView, editor: NSText, delegate: Any?, event: NSEvent?) {
        super.edit(withFrame: drawingRect(forBounds: rect), in: view, editor: editor, delegate: delegate, event: event)
    }
}

final class RoundedSecureTextField: NSSecureTextField {
    var cornerRadius: CGFloat = 10

    override var isEnabled: Bool {
        didSet { needsLayout = true; needsDisplay = true }
    }

    override var focusRingMaskBounds: NSRect { bounds }

    override func drawFocusRingMask() {
        NSBezierPath(roundedRect: bounds, xRadius: cornerRadius, yRadius: cornerRadius).fill()
    }

    override func layout() {
        super.layout()
        layer?.cornerRadius = cornerRadius
        layer?.cornerCurve = .continuous
        layer?.backgroundColor = Palette.fieldBackground(enabled: isEnabled).cgColor
        textColor = isEnabled ? Palette.ink : Palette.muted
        noteFocusRingMaskChanged()
    }
}
