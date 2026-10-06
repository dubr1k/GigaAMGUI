import AppKit
import GigaAMLiquidCore

final class GlassChooserPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

final class GlassPopupButton: NSPopUpButton, NSWindowDelegate {
    override var isEnabled: Bool {
        didSet { needsDisplay = true }
    }

    private var chooser: GlassChooserPanel?
    private var chooserRows: [NSButton] = []
    private var localMouseMonitor: Any?
    private var globalMouseMonitor: Any?
    private var deactivationObserver: NSObjectProtocol?

    override var focusRingMaskBounds: NSRect { bounds }

    override func drawFocusRingMask() {
        NSBezierPath(roundedRect: bounds, xRadius: 10, yRadius: 10).fill()
    }

    override func layout() {
        super.layout()
        noteFocusRingMaskChanged()
    }

    override func mouseDown(with event: NSEvent) { openChooser() }
    override func performClick(_ sender: Any?) { openChooser() }
    override func accessibilityPerformPress() -> Bool { openChooser(); return true }
    override func accessibilityValue() -> Any? { L10n.text(titleOfSelectedItem ?? "") }

    override func keyDown(with event: NSEvent) {
        if [36, 49, 125, 126].contains(event.keyCode) { openChooser() }
        else { super.keyDown(with: event) }
    }

    private func openChooser() {
        guard isEnabled, !itemArray.isEmpty, let parent = window else { return }
        if chooser != nil { closeChooser(); return }
        let width = bounds.width
        // 28 pt rows, 4 pt apart, 8 pt inset: as tall as the items, but the panel is
        // capped to the screen (ChooserPlacement) and the rows scroll inside it.
        let contentHeight = CGFloat(numberOfItems) * 32 + 12
        let anchor = parent.convertToScreen(convert(bounds, to: nil))
        let frame = ChooserPlacement.frame(anchor: anchor, visible: parent.screen?.visibleFrame ?? anchor,
                                           width: width, contentHeight: contentHeight)
        let root = GlassView(radius: 24, overlay: true)
        root.frame = NSRect(origin: .zero, size: frame.size)
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 4
        stack.translatesAutoresizingMaskIntoConstraints = false
        let document = AutoLayoutDocumentView()
        document.translatesAutoresizingMaskIntoConstraints = false
        document.addSubview(stack)
        let scroll = NSScrollView()
        scroll.drawsBackground = false
        scroll.contentView.drawsBackground = false
        scroll.hasVerticalScroller = contentHeight > frame.height
        scroll.autohidesScrollers = true
        scroll.documentView = document
        scroll.translatesAutoresizingMaskIntoConstraints = false
        root.contentView.addSubview(scroll)
        NSLayoutConstraint.activate([
            scroll.leadingAnchor.constraint(equalTo: root.contentView.leadingAnchor, constant: 8),
            scroll.trailingAnchor.constraint(equalTo: root.contentView.trailingAnchor, constant: -8),
            scroll.topAnchor.constraint(equalTo: root.contentView.topAnchor, constant: 8),
            scroll.bottomAnchor.constraint(equalTo: root.contentView.bottomAnchor, constant: -8),
            document.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            document.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            document.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
            stack.leadingAnchor.constraint(equalTo: document.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: document.trailingAnchor),
            stack.topAnchor.constraint(equalTo: document.topAnchor),
            stack.bottomAnchor.constraint(equalTo: document.bottomAnchor)
        ])
        chooserRows.removeAll()
        for (index, item) in itemArray.enumerated() {
            let row = NavigationRowButton(title: L10n.text(item.title), target: self, action: #selector(choose(_:)))
            row.tag = index
            row.acceptsInitialClick = true
            row.setButtonType(.momentaryChange)
            row.keyHandler = { [weak self] event in self?.handleChooserKey(event) ?? false }
            chooserRows.append(row)
            row.isEnabled = item.isEnabled
            row.isBordered = false
            row.font = .systemFont(ofSize: 12)
            row.focusRingType = .none
            row.contentTintColor = Palette.ink
            row.wantsLayer = true
            row.layer?.cornerRadius = 14
            row.layer?.backgroundColor = (index == indexOfSelectedItem ? Palette.blue.withAlphaComponent(0.18) : NSColor.clear).cgColor
            stack.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
            row.heightAnchor.constraint(equalToConstant: 28).isActive = true
        }
        let panel = GlassChooserPanel(contentRect: frame,
                                      styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isReleasedWhenClosed = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .popUpMenu
        panel.contentView = root
        panel.delegate = self
        chooser = panel
        parent.addChildWindow(panel, ordered: .above)
        panel.makeKeyAndOrderFront(nil)
        let mouseEvents: NSEvent.EventTypeMask = [.leftMouseDown, .rightMouseDown, .otherMouseDown]
        localMouseMonitor = NSEvent.addLocalMonitorForEvents(matching: mouseEvents) { [weak self] event in
            guard let self, let panel = self.chooser else { return event }
            let point = event.window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation
            if !panel.frame.contains(point) {
                let controlFrame = self.window?.convertToScreen(self.convert(self.bounds, to: nil))
                self.closeChooser()
                // Consume the trigger click so it cannot immediately reopen the chooser.
                if event.window === self.window && controlFrame?.contains(point) == true { return nil }
            }
            return event
        }
        globalMouseMonitor = NSEvent.addGlobalMonitorForEvents(matching: mouseEvents) { [weak self] _ in
            self?.closeChooser()
        }
        deactivationObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didResignActiveNotification, object: NSApp, queue: .main
        ) { [weak self] _ in self?.closeChooser() }
        if stack.arrangedSubviews.indices.contains(indexOfSelectedItem) {
            let selected = stack.arrangedSubviews[indexOfSelectedItem]
            root.layoutSubtreeIfNeeded()
            selected.scrollToVisible(selected.bounds)
            panel.makeFirstResponder(selected)
        }
    }

    private func closeChooser() {
        if let monitor = localMouseMonitor { NSEvent.removeMonitor(monitor) }
        if let monitor = globalMouseMonitor { NSEvent.removeMonitor(monitor) }
        if let observer = deactivationObserver { NotificationCenter.default.removeObserver(observer) }
        localMouseMonitor = nil
        globalMouseMonitor = nil
        deactivationObserver = nil
        guard let panel = chooser else { return }
        chooser = nil
        chooserRows.removeAll()
        panel.delegate = nil
        panel.parent?.removeChildWindow(panel)
        panel.orderOut(nil)
    }

    func windowDidResignKey(_ notification: Notification) { closeChooser() }

    override func viewWillMove(toWindow newWindow: NSWindow?) {
        if newWindow == nil { closeChooser() }
        super.viewWillMove(toWindow: newWindow)
    }

    private func handleChooserKey(_ event: NSEvent) -> Bool {
        guard let chooserWindow = chooser else { return false }
        if event.keyCode == 53 {
            closeChooser()
            window?.makeKey()
            window?.makeFirstResponder(self)
            return true
        }
        guard let focused = chooserWindow.firstResponder as? NSButton else { return false }
        if [36, 49, 76].contains(event.keyCode) {
            choose(focused)
            return true
        }
        guard [125, 126].contains(event.keyCode) else { return false }
        let enabled = chooserRows.filter { $0.isEnabled }
        guard !enabled.isEmpty else { return true }
        let current = enabled.firstIndex { $0 === focused } ?? 0
        let next = (current + (event.keyCode == 125 ? 1 : enabled.count - 1)) % enabled.count
        chooserWindow.makeFirstResponder(enabled[next])
        enabled[next].scrollToVisible(enabled[next].bounds)
        for row in chooserRows {
            row.layer?.backgroundColor = (row === enabled[next] ? Palette.blue.withAlphaComponent(0.18) : NSColor.clear).cgColor
        }
        return true
    }

    @objc private func choose(_ sender: NSButton) {
        selectItem(at: sender.tag)
        closeChooser()
        window?.makeKey()
        window?.makeFirstResponder(self)
        sendAction(action, to: target)
    }
}

final class GlassPopupCell: NSPopUpButtonCell {
    override func draw(withFrame cellFrame: NSRect, in controlView: NSView) {
        let outline = NSBezierPath(roundedRect: cellFrame.insetBy(dx: 0.5, dy: 0.5), xRadius: 10, yRadius: 10)
        Palette.fieldBackground(enabled: isEnabled).setFill()
        outline.fill()
        Palette.line.withAlphaComponent(0.65).setStroke()
        outline.lineWidth = 0.5
        outline.stroke()
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byTruncatingTail
        let text = NSAttributedString(string: L10n.text(title), attributes: [
            .font: NSFont.systemFont(ofSize: 13),
            .foregroundColor: isEnabled ? Palette.ink : Palette.muted, .paragraphStyle: paragraph
        ])
        text.draw(in: NSRect(x: cellFrame.minX + 10, y: cellFrame.midY - text.size().height / 2,
                            width: max(0, cellFrame.width - 30), height: text.size().height))
        let arrow = NSBezierPath()
        arrow.move(to: NSPoint(x: cellFrame.maxX - 17, y: cellFrame.midY - 2))
        arrow.line(to: NSPoint(x: cellFrame.maxX - 14, y: cellFrame.midY + 1))
        arrow.line(to: NSPoint(x: cellFrame.maxX - 11, y: cellFrame.midY - 2))
        (isEnabled ? Palette.ink : Palette.muted).setStroke()
        arrow.lineWidth = 1
        arrow.stroke()
    }
}
