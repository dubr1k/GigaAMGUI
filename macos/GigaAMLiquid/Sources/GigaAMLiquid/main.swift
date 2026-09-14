import AppKit

private enum Page: String, CaseIterable {
    case processing, result, live, llm, api, history, settings

    var title: String {
        switch self {
        case .processing: return "Обработка"
        case .result: return "Результат обработки"
        case .live: return "Live"
        case .llm: return "LLM"
        case .api: return "API"
        case .history: return "Журнал"
        case .settings: return "Настройки"
        }
    }

    var subtitle: String {
        switch self {
        case .processing: return "Загрузите аудио или видео и настройте параметры."
        case .result: return "Транскрипция и сохранённые файлы обработки."
        case .live: return "Захват в реальном времени и мгновенная транскрипция."
        case .llm: return "Постобработка транскрипций и работа с пользовательскими промптами."
        case .api: return "Рабочая документация и примеры запросов внутри приложения."
        case .history: return "История обработок, статусов и готовых результатов."
        case .settings: return "Параметры приложения, обработки, моделей и путей хранения."
        }
    }

    var navigationTitle: String {
        switch self {
        case .processing: return "Обработка"
        case .result: return "Результат"
        case .live: return "Live"
        case .llm: return "LLM"
        case .api: return "API"
        case .history: return "Журнал"
        case .settings: return "Настройки"
        }
    }

    var symbol: String {
        switch self {
        case .processing: return "waveform"
        case .result: return "doc.text"
        case .live: return "mic"
        case .llm: return "sparkles"
        case .api: return "chevron.left.forwardslash.chevron.right"
        case .history: return "clock"
        case .settings: return "gearshape"
        }
    }
}

private enum Palette {
    static var isDark: Bool { UserDefaults.standard.string(forKey: "settings.theme") == "Тёмная" }
    static var ink: NSColor { isDark ? NSColor(calibratedWhite: 0.94, alpha: 1) : NSColor(calibratedWhite: 0.05, alpha: 1) }
    static var body: NSColor { isDark ? NSColor(calibratedWhite: 0.72, alpha: 1) : NSColor(calibratedRed: 0.22, green: 0.27, blue: 0.33, alpha: 1) }
    static var muted: NSColor { isDark ? NSColor(calibratedWhite: 0.50, alpha: 1) : NSColor(calibratedRed: 0.34, green: 0.38, blue: 0.44, alpha: 1) }
    static var blue: NSColor { isDark ? .white : .black }
    static var line: NSColor { isDark ? NSColor(calibratedWhite: 0.20, alpha: 1) : NSColor(calibratedRed: 0.42, green: 0.46, blue: 0.52, alpha: 1) }
    static var selection: NSColor { isDark ? NSColor(calibratedWhite: 0.16, alpha: 0.98) : NSColor(calibratedWhite: 0.86, alpha: 0.96) }
    static var primary: NSColor { isDark ? NSColor(calibratedWhite: 0.94, alpha: 1) : ink }

    static func fieldBackground(enabled: Bool) -> NSColor {
        NSColor.white.withAlphaComponent(isDark ? (enabled ? 0.16 : 0.04) : (enabled ? 0.52 : 0.20))
    }
}

private final class BlobBackgroundView: NSVisualEffectView {
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        material = .underWindowBackground
        blendingMode = .behindWindow
        state = .active
        wantsLayer = true
        layer?.backgroundColor = NSColor(calibratedWhite: Palette.isDark ? 0.08 : 0.97, alpha: 0.18).cgColor
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}

private final class GlassView: NSView {
    let contentView = NSView()
    private var surfaceRadius: CGFloat = 28

    init(radius: CGFloat = 18, alpha: CGFloat = 0.70, drawsBorder: Bool = true, drawsSurface: Bool = true, overlay: Bool = false) {
        super.init(frame: .zero)
        surfaceRadius = max(radius, 28)
        if !drawsSurface {
            contentView.translatesAutoresizingMaskIntoConstraints = false
            addSubview(contentView)
            NSLayoutConstraint.activate([
                contentView.leadingAnchor.constraint(equalTo: leadingAnchor),
                contentView.trailingAnchor.constraint(equalTo: trailingAnchor),
                contentView.topAnchor.constraint(equalTo: topAnchor),
                contentView.bottomAnchor.constraint(equalTo: bottomAnchor)
            ])
            return
        }
        wantsLayer = true
        layer?.cornerRadius = surfaceRadius
        if overlay {
            layer?.backgroundColor = NSColor(calibratedWhite: Palette.isDark ? 0.12 : 0.97, alpha: 1).cgColor
        }
        layer?.shadowColor = NSColor.black.cgColor
        layer?.shadowOpacity = Palette.isDark ? 0.14 : 0.06
        layer?.shadowRadius = 18
        layer?.shadowOffset = CGSize(width: 0, height: -5)
        layer?.borderWidth = drawsBorder ? 0.5 : 0
        layer?.borderColor = NSColor.white.withAlphaComponent(Palette.isDark ? 0.24 : 0.88).cgColor

        let effect: NSView
        let enabled = UserDefaults.standard.object(forKey: "settings.liquidGlass") == nil || UserDefaults.standard.bool(forKey: "settings.liquidGlass")
        if #available(macOS 26.0, *), enabled {
            let glass = NSGlassEffectView()
            glass.style = overlay ? .regular : .clear
            glass.cornerRadius = surfaceRadius
            glass.tintColor = overlay
                ? NSColor(calibratedWhite: Palette.isDark ? 0.12 : 0.97, alpha: 0.8)
                : (Palette.isDark
                    ? NSColor(calibratedWhite: 0.15, alpha: 0.08)
                    : NSColor.white.withAlphaComponent(min(alpha * 0.12, 0.10)))
            glass.contentView = contentView
            effect = glass
        } else {
            let blur = NSVisualEffectView()
            blur.material = drawsSurface ? .popover : .sidebar
            blur.blendingMode = .withinWindow
            blur.state = .active
            blur.wantsLayer = true
            blur.layer?.cornerRadius = surfaceRadius
            blur.layer?.masksToBounds = true
            blur.addSubview(contentView)
            effect = blur
        }
        effect.translatesAutoresizingMaskIntoConstraints = false
        contentView.translatesAutoresizingMaskIntoConstraints = false
        addSubview(effect)
        NSLayoutConstraint.activate([
            effect.leadingAnchor.constraint(equalTo: leadingAnchor),
            effect.trailingAnchor.constraint(equalTo: trailingAnchor),
            effect.topAnchor.constraint(equalTo: topAnchor),
            effect.bottomAnchor.constraint(equalTo: bottomAnchor),
            contentView.leadingAnchor.constraint(equalTo: effect.leadingAnchor),
            contentView.trailingAnchor.constraint(equalTo: effect.trailingAnchor),
            contentView.topAnchor.constraint(equalTo: effect.topAnchor),
            contentView.bottomAnchor.constraint(equalTo: effect.bottomAnchor)
        ])
    }

    override func layout() {
        super.layout()
        let radius = min(surfaceRadius, bounds.width / 2, bounds.height / 2)
        layer?.cornerRadius = radius
        layer?.cornerCurve = .continuous
        if #available(macOS 26.0, *), let glass = subviews.first as? NSGlassEffectView {
            glass.cornerRadius = radius
        } else {
            subviews.first?.layer?.cornerRadius = radius
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}

private final class CenteredTextCell: NSTextFieldCell {
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

private final class RoundedTextField: NSTextField {
    override var isEnabled: Bool {
        didSet { needsLayout = true; needsDisplay = true }
    }

    override var focusRingMaskBounds: NSRect { bounds }

    override func drawFocusRingMask() {
        NSBezierPath(roundedRect: bounds, xRadius: 10, yRadius: 10).fill()
    }

    override func layout() {
        super.layout()
        layer?.cornerRadius = 10
        layer?.backgroundColor = Palette.fieldBackground(enabled: isEnabled).cgColor
        textColor = isEnabled ? Palette.ink : Palette.muted
        noteFocusRingMaskChanged()
    }
}

private final class CenteredSecureTextCell: NSSecureTextFieldCell {
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

private final class RoundedSecureTextField: NSSecureTextField {
    override var isEnabled: Bool {
        didSet { needsLayout = true; needsDisplay = true }
    }

    override var focusRingMaskBounds: NSRect { bounds }

    override func drawFocusRingMask() {
        NSBezierPath(roundedRect: bounds, xRadius: 10, yRadius: 10).fill()
    }

    override func layout() {
        super.layout()
        layer?.cornerRadius = 10
        layer?.backgroundColor = Palette.fieldBackground(enabled: isEnabled).cgColor
        textColor = isEnabled ? Palette.ink : Palette.muted
        noteFocusRingMaskChanged()
    }
}

private final class FlippedDocumentView: NSView {
    override var isFlipped: Bool { true }

    override func layout() {
        super.layout()
        guard let content = subviews.first else { return }
        let fittingSize = content.fittingSize
        let scroll = enclosingScrollView
        let size = NSSize(width: max(fittingSize.width + 22, scroll?.contentSize.width ?? 0),
                          height: ceil(fittingSize.height))
        if frame.size != size { setFrameSize(size) }
        if let scroll {
            let clip = scroll.contentView
            clip.scroll(to: clip.constrainBoundsRect(clip.bounds).origin)
            scroll.reflectScrolledClipView(clip)
        }
    }
}

private final class EditorScrollView: NSScrollView {
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

private final class EmptyTimelineView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        let frame = NSBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5), xRadius: 12, yRadius: 12)
        NSColor.white.withAlphaComponent(Palette.isDark ? 0.025 : 0.22).setFill()
        frame.fill()
        Palette.line.withAlphaComponent(0.65).setStroke()
        frame.stroke()
        let baseline = NSBezierPath()
        baseline.move(to: NSPoint(x: 18, y: bounds.midY))
        baseline.line(to: NSPoint(x: bounds.width - 18, y: bounds.midY))
        baseline.lineWidth = 1
        baseline.stroke()
    }
}

private final class DropZoneView: NSView {
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

private final class ProgressTrackView: NSView {
    var fraction: Double = 0 { didSet { needsDisplay = true } }

    override func draw(_ dirtyRect: NSRect) {
        let track = NSBezierPath(roundedRect: bounds, xRadius: 4, yRadius: 4)
        (Palette.isDark ? NSColor(calibratedWhite: 0.24, alpha: 1) : NSColor(calibratedWhite: 0.48, alpha: 1)).setFill()
        track.fill()
        Palette.ink.setFill()
        NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: bounds.width * min(1, max(0, fraction)), height: bounds.height), xRadius: 4, yRadius: 4).fill()
    }
}

private final class ApplicationWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}


private final class NavigationRowButton: NSButton {
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

private final class PaddedButton: NSButton {
    var keyHandler: ((NSEvent) -> Bool)?

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

private final class PillSelector: NSControl {
    private var buttons: [NSButton] = []
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityChildren() -> [Any]? { buttons }
    var selectedSegment = -1 { didSet { updateSelection() } }
    override var isEnabled: Bool { didSet { updateSelection() } }

    init(labels: [String], target: AnyObject?, action: Selector?) {
        super.init(frame: .zero)
        cell = NSActionCell()
        isEnabled = true
        self.target = target
        self.action = action
        wantsLayer = true
        layer?.cornerRadius = 16
        layer?.backgroundColor = NSColor.white.withAlphaComponent(Palette.isDark ? 0.06 : 0.30).cgColor
        layer?.borderColor = Palette.line.withAlphaComponent(0.7).cgColor
        layer?.borderWidth = 0.5
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.spacing = 4
        stack.distribution = .fillEqually
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 3),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -3),
            stack.topAnchor.constraint(equalTo: topAnchor, constant: 3),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -3)
        ])
        for (index, title) in labels.enumerated() {
            let button = PaddedButton(title: L10n.text(title), target: self, action: #selector(selectSegment(_:)))
            button.tag = index
            button.keyHandler = { [weak self] event in self?.handleSegmentKey(event, from: index) ?? false }
            button.font = .systemFont(ofSize: 12, weight: .medium)
            button.setButtonType(.pushOnPushOff)
            button.isBordered = false
            button.wantsLayer = true
            button.layer?.cornerRadius = 13
            button.setAccessibilityLabel(L10n.text(title))
            button.setAccessibilityElement(true)
            button.setAccessibilityRole(.button)
            button.setAccessibilityParent(self)
            buttons.append(button)
            stack.addArrangedSubview(button)
            button.heightAnchor.constraint(equalTo: stack.heightAnchor).isActive = true
        }
        updateSelection()
    }

    private func updateSelection() {
        for (index, button) in buttons.enumerated() {
            button.isEnabled = isEnabled
            button.state = index == selectedSegment ? .on : .off
            button.contentTintColor = isEnabled ? (index == selectedSegment ? Palette.blue : Palette.body) : Palette.muted
            button.layer?.backgroundColor = (index == selectedSegment ? Palette.selection : NSColor.clear).cgColor
        }
    }

    private func handleSegmentKey(_ event: NSEvent, from index: Int) -> Bool {
        guard isEnabled, !buttons.isEmpty else { return false }
        if [36, 49, 76].contains(event.keyCode) {
            selectSegment(buttons[index])
            return true
        }
        guard [123, 124, 125, 126].contains(event.keyCode) else { return false }
        let forward = event.keyCode == 124 || event.keyCode == 125
        let next = (index + (forward ? 1 : buttons.count - 1)) % buttons.count
        selectSegment(buttons[next])
        window?.makeFirstResponder(buttons[next])
        return true
    }

    @objc private func selectSegment(_ sender: NSButton) {
        selectedSegment = sender.tag
        sendAction(action, to: target)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}

private final class ContentStackView: NSStackView {
    var fillsWidth = true
    override func addArrangedSubview(_ view: NSView) {
        view.translatesAutoresizingMaskIntoConstraints = false
        super.addArrangedSubview(view)
        if orientation == .vertical && fillsWidth {
            view.widthAnchor.constraint(equalTo: widthAnchor).isActive = true
        }
    }
}

private final class GlassChooserPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

private final class GlassPopupButton: NSPopUpButton, NSWindowDelegate {
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
        let height = CGFloat(numberOfItems) * 32 + 12
        let root = GlassView(radius: 24, overlay: true)
        root.frame = NSRect(x: 0, y: 0, width: width, height: height)
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 4
        stack.translatesAutoresizingMaskIntoConstraints = false
        root.contentView.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: root.contentView.leadingAnchor, constant: 8),
            stack.trailingAnchor.constraint(equalTo: root.contentView.trailingAnchor, constant: -8),
            stack.topAnchor.constraint(equalTo: root.contentView.topAnchor, constant: 8),
            stack.bottomAnchor.constraint(equalTo: root.contentView.bottomAnchor, constant: -8)
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
        let anchor = parent.convertToScreen(convert(bounds, to: nil))
        let visible = parent.screen?.visibleFrame ?? anchor
        let x = min(max(anchor.minX, visible.minX), visible.maxX - width)
        let y = anchor.minY - height - 6 >= visible.minY ? anchor.minY - height - 6 : anchor.maxY + 6
        let panel = GlassChooserPanel(contentRect: NSRect(x: x, y: y, width: width, height: height),
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
            panel.makeFirstResponder(stack.arrangedSubviews[indexOfSelectedItem])
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

private final class RoundedCheckButton: NSButton {
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

private final class ThemedSwitch: NSButton {
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

private final class GlassPopupCell: NSPopUpButtonCell {
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

private final class CircularIconButton: NSButton {
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

private final class AppController: NSObject, NSApplicationDelegate, NSWindowDelegate, NSSearchFieldDelegate, NSTextViewDelegate {
    private let defaults = UserDefaults.standard
    private var window: NSWindow!
    private var mainSurface: GlassView!
    private var pageTitle: NSTextField!
    private var pageSubtitle: NSTextField!
    private var pageScroll: NSScrollView!
    private var navigationButtons: [Page: NSButton] = [:]
    private var selectedFilesLabel: NSTextField?
    private var settingsCategoryButtons: [String: NSButton] = [:]
    private var currentPage: Page = .processing
    private var settingsDetail: NSView?
    private var transcriptEditor: NSTextView?
    private var promptEditor: NSTextView?
    private var apiCodeText: NSTextView?
    private var resultTranscript: NSTextView?
    private var resultPages: [(key: String, title: String, text: String)] = []
    private var selectedResultTab = "transcript"
    private var selectedResultURL: URL?
    private var selectedOutputFormat: String?
    private var transcriptionResults: [NativeTranscriptionResult] = []
    private var transcriptionJob: NativeTranscriptionJob?
    private var transcriptionFiles: [URL] = []
    private var fileStates: [URL: String] = [:]
    private var cancellationRequested = false
    private var transcriptionProgress: Double? = 0
    private var transcriptionStatus = "Нет активных задач"
    private var transcriptionLog = ""
    private weak var processingValidationLabel: NSTextField?
    private weak var startProcessingButton: NSButton?
    private weak var cancelProcessingButton: NSButton?
    private weak var progressTrack: ProgressTrackView?
    private weak var progressPercentage: NSTextField?
    private weak var progressStatus: NSTextField?
    private var selectedFileURLs: [URL] = []
    private var downloadedMediaRoots = Set<URL>()
    private var mediaDownloadJob: MediaDownloadJob?
    private var mediaImportAlert: NSAlert?
    private weak var mediaImportButton: NSButton?
    private var isClosing = false
    private var isTerminating = false
    private var searchField: NSSearchField?
    private var searchResults = NSView()
    private var searchCapsule: GlassView?
    private var searchWidth: NSLayoutConstraint?

    func applicationDidFinishLaunching(_ notification: Notification) {
        for (old, current) in [("settings.sentences", "subtitle.sentences"), ("settings.subtitleLines", "subtitle.lines"), ("settings.subtitleCharacters", "subtitle.characters")] {
            if defaults.object(forKey: current) == nil, let value = defaults.object(forKey: old) { defaults.set(value, forKey: current) }
            defaults.removeObject(forKey: old)
        }
        if let legacyToken = defaults.string(forKey: "settings.hfToken"), !legacyToken.isEmpty {
            try? SecureStore.set(legacyToken, for: "hfToken")
        }
        defaults.removeObject(forKey: "settings.hfToken")
        cleanupDownloadedMedia()
        buildWindow()
        show(page: .processing)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildWindow() {
        window = ApplicationWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 900), styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.title = "GigaAM v3"
        window.delegate = self
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.isMovableByWindowBackground = true
        window.hasShadow = false
        window.isOpaque = false
        window.backgroundColor = .clear
        window.minSize = NSSize(width: 1040, height: 700)
        window.center()
        buildWindowContent()
    }

    private func buildWindowContent() {
        navigationButtons.removeAll()
        selectedFilesLabel = nil
        settingsCategoryButtons.removeAll()
        window.appearance = NSAppearance(named: Palette.isDark ? .darkAqua : .aqua)
        let background = BlobBackgroundView()
        background.translatesAutoresizingMaskIntoConstraints = false
        window.contentView = background

        let shell = NSStackView()
        shell.orientation = .horizontal
        shell.spacing = 12
        shell.translatesAutoresizingMaskIntoConstraints = false
        background.addSubview(shell)
        NSLayoutConstraint.activate([
            shell.leadingAnchor.constraint(equalTo: background.leadingAnchor, constant: 18),
            shell.trailingAnchor.constraint(equalTo: background.trailingAnchor, constant: -18),
            shell.topAnchor.constraint(equalTo: background.topAnchor, constant: 18),
            shell.bottomAnchor.constraint(equalTo: background.bottomAnchor, constant: -18)
        ])

        let sidebar = buildSidebar()
        sidebar.widthAnchor.constraint(equalToConstant: 226).isActive = true
        mainSurface = GlassView(drawsBorder: false, drawsSurface: false)
        shell.addArrangedSubview(sidebar)
        shell.addArrangedSubview(mainSurface)
        sidebar.heightAnchor.constraint(equalTo: shell.heightAnchor).isActive = true
        mainSurface.heightAnchor.constraint(equalTo: shell.heightAnchor).isActive = true
        mainSurface.widthAnchor.constraint(equalTo: shell.widthAnchor, constant: -238).isActive = true
        buildMainSurface()
    }

    private func buildSidebar() -> NSView {
        let surface = GlassView(drawsBorder: false, drawsSurface: false)
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        surface.contentView.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: surface.leadingAnchor, constant: 12),
            stack.trailingAnchor.constraint(equalTo: surface.trailingAnchor, constant: -12),
            stack.topAnchor.constraint(equalTo: surface.topAnchor, constant: 30),
            stack.bottomAnchor.constraint(equalTo: surface.bottomAnchor, constant: -28)
        ])


        let brand = NSStackView()
        brand.orientation = .horizontal
        brand.alignment = .centerY
        brand.spacing = 12
        let icon = brandMark()
        icon.widthAnchor.constraint(equalToConstant: 36).isActive = true
        let subtitle = label("Транскрибация аудио и видео", size: 14, color: Palette.body)
        subtitle.maximumNumberOfLines = 2
        subtitle.lineBreakMode = .byWordWrapping
        let brandText = NSStackView(views: [label("GigaAM v3", size: 28, weight: .medium, color: Palette.ink), subtitle])
        brandText.orientation = .vertical
        brandText.spacing = 3
        brandText.widthAnchor.constraint(equalToConstant: 150).isActive = true
        brand.addArrangedSubview(icon)
        brand.addArrangedSubview(brandText)
        stack.addArrangedSubview(brand)

        let navGroup = NSStackView()
        navGroup.orientation = .vertical
        navGroup.alignment = .leading
        navGroup.spacing = 8
        for page in Page.allCases where page != .result {
            let button = navigationButton(for: page)
            navigationButtons[page] = button
            navGroup.addArrangedSubview(button)
        }
        stack.setCustomSpacing(28, after: brand)
        stack.addArrangedSubview(navGroup)
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .vertical)
        stack.addArrangedSubview(spacer)

        stack.addArrangedSubview(label("GigaAMGUI v3", size: 13, color: Palette.body))
        let tagline = label("Локально. Быстро. Точно.", size: 12, color: Palette.muted)
        tagline.maximumNumberOfLines = 2
        tagline.lineBreakMode = .byWordWrapping
        stack.addArrangedSubview(tagline)
        return surface
    }


    private func brandMark() -> NSView {
        let view = NSView()
        view.wantsLayer = true
        let colors = [Palette.blue, NSColor.black, Palette.ink]
        let heights: [CGFloat] = [30, 46, 24]
        for index in 0..<3 {
            let bar = NSView()
            bar.wantsLayer = true
            bar.layer?.backgroundColor = colors[index].cgColor
            bar.layer?.cornerRadius = 3
            bar.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(bar)
            NSLayoutConstraint.activate([
                bar.widthAnchor.constraint(equalToConstant: 6),
                bar.heightAnchor.constraint(equalToConstant: heights[index]),
                bar.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: CGFloat(index) * 12),
                bar.centerYAnchor.constraint(equalTo: view.centerYAnchor)
            ])
        }
        return view
    }

    private func navigationButton(for page: Page) -> NSButton {
        let button = NavigationRowButton(title: L10n.text(page.navigationTitle), target: self, action: #selector(navigate(_:)))
        button.identifier = NSUserInterfaceItemIdentifier(page.rawValue)
        button.image = NSImage(systemSymbolName: page.symbol, accessibilityDescription: L10n.text(page.navigationTitle))
        button.imagePosition = .imageLeading
        button.imageScaling = .scaleProportionallyDown
        button.contentTintColor = Palette.body
        button.font = NSFont.systemFont(ofSize: 14, weight: .regular)
        button.alignment = .left
        button.bezelStyle = .rounded
        button.isBordered = false
        button.wantsLayer = true
        button.layer?.cornerRadius = 12
        button.translatesAutoresizingMaskIntoConstraints = false
        button.heightAnchor.constraint(equalToConstant: 44).isActive = true
        button.widthAnchor.constraint(equalToConstant: 202).isActive = true
        return button
    }

    private func buildMainSurface() {
        let header = NSStackView()
        header.orientation = .horizontal
        header.alignment = .centerY
        header.spacing = 10
        header.translatesAutoresizingMaskIntoConstraints = false
        mainSurface.contentView.addSubview(header)

        let capsule = GlassView(radius: 22, alpha: 0.70)
        capsule.translatesAutoresizingMaskIntoConstraints = false
        capsule.contentView.wantsLayer = true
        capsule.contentView.layer?.cornerRadius = 22
        capsule.contentView.layer?.masksToBounds = true
        let width = capsule.widthAnchor.constraint(equalToConstant: 44)
        width.isActive = true
        capsule.heightAnchor.constraint(equalToConstant: 44).isActive = true
        searchCapsule = capsule
        searchWidth = width
        let searchButton = NSButton(image: NSImage(systemSymbolName: "magnifyingglass", accessibilityDescription: L10n.text("Поиск"))!, target: self, action: #selector(toggleSearch(_:)))
        searchButton.isBordered = false
        searchButton.imagePosition = .imageOnly
        searchButton.contentTintColor = Palette.ink
        searchButton.setAccessibilityLabel(L10n.text("Поиск"))
        searchButton.identifier = NSUserInterfaceItemIdentifier("navigation.search")
        searchButton.translatesAutoresizingMaskIntoConstraints = false
        capsule.contentView.addSubview(searchButton)
        let search = NSSearchField()
        search.placeholderString = L10n.text("Поиск разделов")
        search.setAccessibilityLabel(L10n.text("Поиск разделов"))
        search.delegate = self
        search.font = NSFont.systemFont(ofSize: 14)
        search.isBordered = false
        search.drawsBackground = false
        search.focusRingType = .none
        (search.cell as? NSSearchFieldCell)?.searchButtonCell = nil
        search.translatesAutoresizingMaskIntoConstraints = false
        search.isHidden = true
        searchField = search
        capsule.contentView.addSubview(search)
        NSLayoutConstraint.activate([
            searchButton.leadingAnchor.constraint(equalTo: capsule.contentView.leadingAnchor),
            searchButton.centerYAnchor.constraint(equalTo: capsule.contentView.centerYAnchor),
            searchButton.widthAnchor.constraint(equalToConstant: 44),
            searchButton.heightAnchor.constraint(equalToConstant: 44),
            search.leadingAnchor.constraint(equalTo: capsule.contentView.leadingAnchor, constant: 44),
            search.centerYAnchor.constraint(equalTo: capsule.contentView.centerYAnchor),
            search.widthAnchor.constraint(equalToConstant: 284),
            search.heightAnchor.constraint(equalToConstant: 20)
        ])
        header.addArrangedSubview(capsule)
        let flexible = NSView()
        flexible.setContentHuggingPriority(.defaultLow, for: .horizontal)
        header.addArrangedSubview(flexible)
        header.addArrangedSubview(iconButton("moon", hint: "Переключить тему", action: #selector(toggleDarkTheme(_:))))
        let languageButton = pill(L10n.isEnglish ? "EN" : "RU", width: 64, action: #selector(toggleLanguage(_:)))
        languageButton.identifier = NSUserInterfaceItemIdentifier("settings.language.toggle")
        languageButton.setAccessibilityLabel(L10n.text("Переключить язык"))
        header.addArrangedSubview(languageButton)

        let titleBlock = NSStackView()
        titleBlock.orientation = .vertical
        titleBlock.alignment = .leading
        titleBlock.spacing = 5
        titleBlock.translatesAutoresizingMaskIntoConstraints = false
        pageTitle = label("", size: 28, weight: .medium, color: Palette.ink)
        pageSubtitle = label("", size: 14, color: Palette.body)
        pageSubtitle.maximumNumberOfLines = 2
        pageSubtitle.lineBreakMode = .byWordWrapping
        titleBlock.addArrangedSubview(pageTitle)
        titleBlock.addArrangedSubview(pageSubtitle)
        mainSurface.contentView.addSubview(titleBlock)

        pageScroll = NSScrollView()
        pageScroll.drawsBackground = false
        pageScroll.hasVerticalScroller = false
        pageScroll.autohidesScrollers = true
        pageScroll.translatesAutoresizingMaskIntoConstraints = false
        mainSurface.contentView.addSubview(pageScroll)

        NSLayoutConstraint.activate([
            header.leadingAnchor.constraint(equalTo: mainSurface.leadingAnchor, constant: 26),
            header.trailingAnchor.constraint(equalTo: mainSurface.trailingAnchor, constant: -20),
            header.topAnchor.constraint(equalTo: mainSurface.topAnchor, constant: 14),
            header.heightAnchor.constraint(equalToConstant: 48),
            titleBlock.leadingAnchor.constraint(equalTo: mainSurface.leadingAnchor, constant: 26),
            titleBlock.topAnchor.constraint(equalTo: header.bottomAnchor, constant: 18),
            titleBlock.trailingAnchor.constraint(lessThanOrEqualTo: mainSurface.trailingAnchor, constant: -30),
            pageScroll.leadingAnchor.constraint(equalTo: mainSurface.leadingAnchor, constant: 26),
            pageScroll.topAnchor.constraint(equalTo: titleBlock.bottomAnchor, constant: 16),
            pageScroll.trailingAnchor.constraint(equalTo: mainSurface.trailingAnchor),
            pageScroll.bottomAnchor.constraint(equalTo: mainSurface.bottomAnchor, constant: -6)
        ])
    }

    @objc private func navigate(_ sender: NSButton) {
        guard let id = sender.identifier?.rawValue, let page = Page(rawValue: id) else { return }
        show(page: page)
    }

    private func show(page: Page) {
        currentPage = page
        pageTitle.stringValue = L10n.text(page.title)
        pageSubtitle.stringValue = L10n.text(page.subtitle)
        for (candidate, button) in navigationButtons {
            let selected = candidate == page
            button.wantsLayer = true
            button.layer?.backgroundColor = selected ? Palette.selection.cgColor : NSColor.clear.cgColor
            button.contentTintColor = selected ? Palette.blue : Palette.body
            button.font = NSFont.systemFont(ofSize: 14, weight: selected ? .medium : .regular)
        }
        let document = scrollDocument(for: page)
        pageScroll.documentView = document
        pageScroll.hasHorizontalScroller = true
        pageScroll.hasVerticalScroller = true
        document.needsLayout = true
        document.layoutSubtreeIfNeeded()
        pageScroll.contentView.scroll(to: .zero)
        pageScroll.reflectScrolledClipView(pageScroll.contentView)
        refreshProcessingControls()
    }

    private func scrollDocument(for page: Page) -> NSView {
        let document = FlippedDocumentView(frame: NSRect(x: 0, y: 0, width: 878, height: 0))
        document.autoresizingMask = [.width]
        let content = vertical([], spacing: 16)
        content.translatesAutoresizingMaskIntoConstraints = false
        document.addSubview(content)
        NSLayoutConstraint.activate([
            content.leadingAnchor.constraint(equalTo: document.leadingAnchor),
            content.topAnchor.constraint(equalTo: document.topAnchor),
            content.widthAnchor.constraint(equalToConstant: 878)
        ])
        switch page {
        case .processing: buildProcessing(into: content)
        case .result: buildResult(into: content)
        case .live: buildLive(into: content)
        case .llm: buildLLM(into: content)
        case .api: buildAPI(into: content)
        case .history: buildHistory(into: content)
        case .settings: buildSettings(into: content)
        }
        return document
    }

    private func buildProcessing(into content: NSStackView) {
        let upload = card("Загрузка файлов")
        let zone = DropZoneView()
        let zoneText = vertical([
            symbol("square.and.arrow.up", size: 28),
            label("Перетащите сюда аудио или видео", size: 17, weight: .regular, color: Palette.ink),
            label(".wav, .mp3, .m4a, .mp4, .mov, .mkv", size: 13, color: Palette.body)
        ], spacing: 8, alignment: .centerX)
        zoneText.translatesAutoresizingMaskIntoConstraints = false
        zone.addSubview(zoneText)
        NSLayoutConstraint.activate([
            zoneText.centerXAnchor.constraint(equalTo: zone.centerXAnchor),
            zoneText.centerYAnchor.constraint(equalTo: zone.centerYAnchor),
            zone.heightAnchor.constraint(equalToConstant: 116)
        ])
        let choose = button("Выбрать файлы", primary: true, action: #selector(chooseFiles(_:)))
        choose.identifier = NSUserInterfaceItemIdentifier("processing.choose")
        choose.widthAnchor.constraint(equalToConstant: 140).isActive = true
        let link = button("Ссылка на медиа", action: #selector(chooseMediaURL(_:)))
        link.identifier = NSUserInterfaceItemIdentifier("processing.media")
        link.isEnabled = mediaDownloadJob == nil && transcriptionJob == nil
        mediaImportButton = link
        link.widthAnchor.constraint(equalToConstant: 140).isActive = true
        let uploadStack = contentStack(upload)
        uploadStack.addArrangedSubview(zone)
        uploadStack.addArrangedSubview(centered(horizontal([choose, link], spacing: 12)))
        size(upload, width: 610, height: 238)

        let processing = card("Настройки обработки", dense: true)
        let settings = contentStack(processing)
        settings.spacing = 12
        settings.addArrangedSubview(compactField("Подготовка аудио", control: popup(["auto", "off", "light", "denoise"], key: "processing.preprocessing")))
        settings.addArrangedSubview(compactField("Модель", control: popup(["v3_e2e_rnnt", "multilingual_ctc", "multilingual_large_ctc"], key: "settings.model")))
        settings.addArrangedSubview(toggleRow("Диаризация", key: "settings.diarization", defaultValue: false))
        settings.addArrangedSubview(compactField("Кол-во спикеров", control: popup(["Авто", "1", "2", "3", "4", "5", "6"], key: "processing.speakers")))
        processing.widthAnchor.constraint(equalToConstant: 252).isActive = true
        settings.bottomAnchor.constraint(equalTo: processing.bottomAnchor, constant: -16).isActive = true
        content.addArrangedSubview(horizontal([upload, processing], spacing: 16))

        let clear = button("Очистить", action: #selector(clearFiles(_:)), height: 30)
        clear.identifier = NSUserInterfaceItemIdentifier("processing.clear")
        let selected = card("Выбранные файлы", trailing: clear)
        let selectedStack = contentStack(selected)
        selectedStack.addArrangedSubview(columnHeadings([("Файл", 340), ("Состояние", 200)]))
        selectedStack.addArrangedSubview(divider())
        let filenames = wrappedLabel("Файлы не выбраны. Добавьте аудио или видео.", size: 14, color: Palette.body)
        filenames.preferredMaxLayoutWidth = 574
        selectedFilesLabel = filenames
        selectedStack.addArrangedSubview(filenames)
        refreshSelectedFiles()
        selected.widthAnchor.constraint(equalToConstant: 610).isActive = true
        selectedStack.bottomAnchor.constraint(equalTo: selected.bottomAnchor, constant: -16).isActive = true

        let folder = compactCard("Папка сохранения результатов")
        let path = editableText(defaults.string(forKey: "output.path") ?? "~/Documents/GigaAM", key: "output.path", placeholder: "Выберите папку")
        path.heightAnchor.constraint(equalToConstant: 36).isActive = true
        path.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let change = button("Изменить", action: #selector(chooseOutputFolder(_:)), height: 36)
        change.identifier = NSUserInterfaceItemIdentifier("output.choose")
        change.widthAnchor.constraint(equalToConstant: 92).isActive = true
        contentStack(folder).addArrangedSubview(horizontal([path, change], spacing: 12))
        folder.widthAnchor.constraint(equalToConstant: 610).isActive = true
        contentStack(folder).bottomAnchor.constraint(equalTo: folder.bottomAnchor, constant: -16).isActive = true

        let output = card("Форматы вывода", dense: true)
        let formats = contentStack(output)
        formats.spacing = 7
        formats.addArrangedSubview(equalColumns([
            checkbox("Текст (.txt)", key: "output.txt", defaultValue: true),
            checkbox("Таймкоды", key: "output.timestamps", defaultValue: true)
        ], spacing: 6))
        formats.addArrangedSubview(equalColumns([
            checkbox("Markdown", key: "output.md", defaultValue: false),
            checkbox("SRT (.srt)", key: "output.srt", defaultValue: false)
        ], spacing: 6))
        formats.addArrangedSubview(checkbox("VTT (.vtt)", key: "output.vtt", defaultValue: false))
        formats.addArrangedSubview(divider())
        formats.addArrangedSubview(label("Настройки субтитров", size: 15, weight: .medium, color: Palette.ink))
        formats.addArrangedSubview(equalColumns([
            compactField("Строк в блоке", control: popup(["2", "1", "3", "4"], key: "subtitle.lines")),
            compactField("Символов", control: popup(["64", "42", "80"], key: "subtitle.characters"))
        ], spacing: 8))
        formats.addArrangedSubview(toggleRow("Разбивать по предложениям", key: "subtitle.sentences", defaultValue: true))
        output.widthAnchor.constraint(equalToConstant: 252).isActive = true
        formats.bottomAnchor.constraint(equalTo: output.bottomAnchor, constant: -16).isActive = true
        content.addArrangedSubview(horizontal([vertical([selected, folder], spacing: 16), output], spacing: 16))
        let start = button("Запустить обработку", primary: true, action: #selector(startProcessing(_:)), height: 52)
        start.identifier = NSUserInterfaceItemIdentifier("transcription.start")
        startProcessingButton = start
        let cancel = button("Остановить после текущего файла", action: #selector(cancelProcessing(_:)), height: 52)
        cancel.identifier = NSUserInterfaceItemIdentifier("transcription.cancel")
        cancelProcessingButton = cancel
        content.addArrangedSubview(vertical([start, cancel], spacing: 12))
        let validation = wrappedLabel("", size: 12, color: Palette.muted)
        validation.identifier = NSUserInterfaceItemIdentifier("transcription.validation")
        processingValidationLabel = validation
        content.addArrangedSubview(validation)
        content.addArrangedSubview(progressCard())
    }

    private func buildResult(into content: NSStackView) {
        let selected = currentResult
        let outputs = existingOutputs
        let result = card("Результат обработки")
        let body = contentStack(result)
        body.spacing = 12
        if !transcriptionResults.isEmpty {
            let files = GlassPopupButton()
            files.cell = GlassPopupCell(textCell: "", pullsDown: false)
            for (index, item) in transcriptionResults.enumerated() {
                files.addItem(withTitle: "\(index + 1). \(item.inputURL.lastPathComponent)")
                files.lastItem?.toolTip = item.inputURL.path
            }
            files.selectItem(at: transcriptionResults.firstIndex { $0.inputURL == selected?.inputURL } ?? 0)
            files.target = self
            files.action = #selector(resultFileChanged(_:))
            files.identifier = NSUserInterfaceItemIdentifier("result.file")
            files.setAccessibilityLabel(L10n.text("Выбранные файлы"))
            body.addArrangedSubview(files)
        }
        let status = wrappedLabel(selected?.error ?? (selected == nil ? "Транскрипция появится после обработки файла." : (outputs.isEmpty ? "Результат получен. Сохранённые файлы недоступны." : "Готовые файлы сохранены на диске.")), size: 12, color: Palette.body)
        status.maximumNumberOfLines = 3
        status.toolTip = selected?.error ?? selected?.inputURL.path
        body.addArrangedSubview(status)
        resultPages = []
        if let selected {
            if !selected.transcript.isEmpty { resultPages.append(("transcript", "Транскрипция", selected.transcript)) }
            let subtitle: (String, String) = outputs["srt"] == nil ? ("vtt", "VTT") : ("srt", "SRT")
            let diarization = outputs["txt_diarize"] == nil ? "txt_diarize_timecodes" : "txt_diarize"
            for (key, title) in [("txt_timecodes", "Таймкоды"), subtitle, (diarization, "Диаризация"), ("md", "Markdown")] {
                if let url = outputs[key], let text = try? String(contentsOf: url, encoding: .utf8), !text.isEmpty {
                    resultPages.append((key, title, text))
                }
            }
            if !selected.metadataJSON.isEmpty { resultPages.append(("json", "JSON", selected.metadataJSON)) }
        }
        let selectedTab = resultPages.firstIndex { $0.key == selectedResultTab } ?? 0
        if resultPages.indices.contains(selectedTab) { selectedResultTab = resultPages[selectedTab].key }
        let tabs = PillSelector(labels: resultPages.isEmpty ? ["Транскрипция"] : resultPages.map(\.title), target: self, action: #selector(resultTabChanged(_:)))
        tabs.selectedSegment = selectedTab
        tabs.isEnabled = !resultPages.isEmpty
        tabs.identifier = NSUserInterfaceItemIdentifier("result.tabs")
        tabs.heightAnchor.constraint(equalToConstant: 32).isActive = true
        body.addArrangedSubview(tabs)
        let editor = textEditor(resultPages.isEmpty ? L10n.text("Транскрипция появится после обработки файла.") : resultPages[selectedTab].text, key: nil, height: 410)
        resultTranscript = editor.documentView as? NSTextView
        resultTranscript?.isEditable = false
        resultTranscript?.identifier = NSUserInterfaceItemIdentifier("result.content")
        resultTranscript?.setAccessibilityLabel(L10n.text("Результат обработки"))
        body.addArrangedSubview(editor)
        result.widthAnchor.constraint(equalToConstant: 646).isActive = true
        body.bottomAnchor.constraint(equalTo: result.bottomAnchor, constant: -16).isActive = true

        let useful = card("Полезное")
        let actions = contentStack(useful)
        actions.spacing = 12
        for (title, identifier, selector) in [("Копировать текст", "result.copy", #selector(copyResult(_:))), ("Использовать в LLM", "result.llm", #selector(useResultForLLM(_:)))] {
            let action = button(title, action: selector, height: 34)
            action.identifier = NSUserInterfaceItemIdentifier(identifier)
            action.isEnabled = !(selected?.transcript.isEmpty ?? true)
            actions.addArrangedSubview(action)
        }
        actions.addArrangedSubview(divider())
        actions.addArrangedSubview(label("Сохранённые файлы", size: 14, weight: .medium, color: Palette.ink))
        let formats = outputs.keys.sorted()
        if !formats.contains(selectedOutputFormat ?? "") { selectedOutputFormat = formats.first }
        let exports = GlassPopupButton()
        exports.cell = GlassPopupCell(textCell: "", pullsDown: false)
        exports.addItems(withTitles: formats.isEmpty ? [L10n.text("Нет файлов")] : formats)
        exports.selectItem(withTitle: selectedOutputFormat ?? L10n.text("Нет файлов"))
        exports.target = self
        exports.action = #selector(resultOutputChanged(_:))
        exports.identifier = NSUserInterfaceItemIdentifier("result.output")
        exports.isEnabled = !formats.isEmpty
        actions.addArrangedSubview(exports)
        for (title, identifier, selector) in [("Открыть файл", "result.open", #selector(openResultOutput(_:))), ("Показать в Finder", "result.reveal", #selector(revealResultOutputs(_:)))] {
            let action = button(title, action: selector, height: 34)
            action.identifier = NSUserInterfaceItemIdentifier(identifier)
            action.isEnabled = !formats.isEmpty
            actions.addArrangedSubview(action)
        }
        actions.addArrangedSubview(wrappedLabel("Доступны только фактические результаты. LLM-анализ и воспроизведение не подключены.", size: 12, color: Palette.muted))
        useful.widthAnchor.constraint(equalToConstant: 216).isActive = true
        actions.bottomAnchor.constraint(equalTo: useful.bottomAnchor, constant: -16).isActive = true
        content.addArrangedSubview(horizontal([result, useful], spacing: 16))
    }

    private func buildLive(into content: NSStackView) {
        let source = card("Источник аудио", dense: true)
        let sourceBody = contentStack(source)
        sourceBody.spacing = 5
        let microphone = popup(["Не подключён"], key: "live.microphone")
        microphone.isEnabled = false
        sourceBody.addArrangedSubview(compactField("Микрофон", control: microphone))
        sourceBody.addArrangedSubview(compactField("Системный звук", control: popup(["Выкл.", "Включено"], key: "live.systemAudio")))
        sourceBody.addArrangedSubview(toggleRow("Записывать микрофон", key: "live.recordMic", defaultValue: true))
        sourceBody.addArrangedSubview(toggleRow("Записывать системный звук", key: "live.recordSystem", defaultValue: false))
        sourceBody.addArrangedSubview(compactField("Диаризация", control: popup(["Включено", "Выключено"], key: "live.diarization")))
        size(source, width: 314, height: 266)

        let recorder = card("Запись")
        let recorderBody = contentStack(recorder)
        recorderBody.spacing = 9
        recorderBody.addArrangedSubview(centered(label("00:00:00", size: 28, weight: .medium, color: Palette.ink)))
        recorderBody.addArrangedSubview(centered(label("Запись не начата", size: 13, color: Palette.body)))
        let meter = EmptyTimelineView()
        meter.heightAnchor.constraint(equalToConstant: 58).isActive = true
        recorderBody.addArrangedSubview(meter)
        recorderBody.addArrangedSubview(centered(horizontal([
            unavailableIcon("record.circle", hint: "Начать запись: рабочий сервис не подключён"),
            unavailableIcon("stop.fill", hint: "Остановить запись"),
            unavailableIcon("pause.fill", hint: "Приостановить запись")
        ], spacing: 8)))
        size(recorder, width: 262, height: 266)

        let parameters = card("Параметры", dense: true)
        let parametersBody = contentStack(parameters)
        parametersBody.spacing = 5
        parametersBody.addArrangedSubview(compactField("Язык аудио", control: popup(["Русский", "Авто"], key: "live.language")))
        parametersBody.addArrangedSubview(compactField("Движок", control: popup(["Pyannote 3.1", "Выключен"], key: "live.engine")))
        parametersBody.addArrangedSubview(compactField("Кол-во спикеров", control: popup(["Авто", "1", "2", "3"], key: "live.speakers")))
        parametersBody.addArrangedSubview(equalColumns([
            checkbox("TXT (.txt)", key: "live.txt", defaultValue: true),
            checkbox("SRT (.srt)", key: "live.srt", defaultValue: true)
        ], spacing: 8))
        parametersBody.addArrangedSubview(checkbox("Разбивать по предложениям", key: "live.sentences", defaultValue: true))
        size(parameters, width: 270, height: 266)
        content.addArrangedSubview(horizontal([source, recorder, parameters], spacing: 16))

        let transcript = card("Live transcript")
        let transcriptBody = contentStack(transcript)
        transcriptBody.addArrangedSubview(columnHeadings([("Время", 76), ("Спикер", 90), ("Транскрипция", 500)]))
        transcriptBody.addArrangedSubview(divider())
        let empty = wrappedLabel("Нет фрагментов. Захват аудио не подключён.", size: 14, color: Palette.body)
        empty.heightAnchor.constraint(equalToConstant: 196).isActive = true
        transcriptBody.addArrangedSubview(empty)
        let save = unavailableButton("Сохранить запись")
        let done = unavailableButton("Готово", primary: true)
        save.setContentCompressionResistancePriority(.required, for: .horizontal)
        done.setContentCompressionResistancePriority(.required, for: .horizontal)
        let actions = horizontal([flexibleSpace(), save, done], spacing: 12)
        actions.alignment = .centerY
        actions.heightAnchor.constraint(equalToConstant: 38).isActive = true
        transcriptBody.addArrangedSubview(actions)
        size(transcript, width: 878, height: 354)
        content.addArrangedSubview(transcript)
    }

    private func buildLLM(into content: NSStackView) {
        let source = card("Исходный текст")
        let sourceBody = contentStack(source)
        sourceBody.spacing = 10
        sourceBody.addArrangedSubview(button("Выбрать файл с транскриптом", action: #selector(chooseTranscript(_:))))
        sourceBody.addArrangedSubview(compactField("Провайдер", control: popup(["OpenAI-compatible", "Anthropic", "Локальный"], key: "llm.provider")))
        sourceBody.addArrangedSubview(compactField("Модель", control: editableText(defaults.string(forKey: "llm.model") ?? "gpt-4.1-mini", key: "llm.model", placeholder: "Модель")))
        sourceBody.addArrangedSubview(label("Транскрипция · можно вставить текст", size: 12, color: Palette.body))
        let editor = textEditor(defaults.string(forKey: "llm.source") ?? "", key: "llm.source", height: 180)
        transcriptEditor = editor.documentView as? NSTextView
        transcriptEditor?.setAccessibilityLabel(L10n.text("Исходная транскрипция"))
        sourceBody.addArrangedSubview(editor)
        let sourceNote = wrappedLabel("Текст и параметры сохраняются на этом Mac.", size: 12, color: Palette.muted)
        sourceNote.heightAnchor.constraint(equalToConstant: 38).isActive = true
        sourceBody.addArrangedSubview(sourceNote)
        source.widthAnchor.constraint(equalToConstant: 878).isActive = true
        sourceBody.bottomAnchor.constraint(equalTo: source.bottomAnchor, constant: -16).isActive = true

        let templates = card("Шаблоны")
        let templatesBody = contentStack(templates)
        templatesBody.spacing = 10
        templatesBody.addArrangedSubview(template("Краткое содержание", "Сжать текст в тезисы"))
        templatesBody.addArrangedSubview(template("Извлечение задач", "Выделить action items"))
        templatesBody.addArrangedSubview(template("Свой промпт", "Использовать инструкцию"))
        templatesBody.addArrangedSubview(wrappedLabel("Пользовательский промпт", size: 12, color: Palette.muted))
        let prompt = textEditor(defaults.string(forKey: "llm.prompt") ?? "", key: "llm.prompt", height: 140)
        promptEditor = prompt.documentView as? NSTextView
        promptEditor?.setAccessibilityLabel(L10n.text("Пользовательский промпт"))
        templatesBody.addArrangedSubview(prompt)
        templatesBody.addArrangedSubview(wrappedLabel("LLM-сервис не подключён. Запросы не отправляются.", size: 12, color: Palette.muted))
        let run = unavailableButton("Запустить обработку", primary: true, reason: "LLM-сервис не подключён.")
        run.font = .systemFont(ofSize: 12)
        templatesBody.addArrangedSubview(run)
        templates.widthAnchor.constraint(equalToConstant: 878).isActive = true
        templatesBody.bottomAnchor.constraint(equalTo: templates.bottomAnchor, constant: -16).isActive = true

        let output = card("Результат")
        let outputBody = contentStack(output)
        let result = insetPanel()
        embed(vertical([
            label("Краткое содержание", size: 15, weight: .medium, color: Palette.ink),
            wrappedLabel("Ответа пока нет. Здесь появится результат запроса к выбранному провайдеру.", size: 14, color: Palette.body)
        ], spacing: 18), in: result, inset: 16)
        result.heightAnchor.constraint(equalToConstant: 150).isActive = true
        outputBody.addArrangedSubview(result)
        outputBody.addArrangedSubview(equalColumns([unavailableButton("Копировать"), unavailableButton("Скачать .md")], spacing: 12))
        output.widthAnchor.constraint(equalToConstant: 878).isActive = true
        outputBody.bottomAnchor.constraint(equalTo: output.bottomAnchor, constant: -16).isActive = true
        content.addArrangedSubview(source)
        content.addArrangedSubview(templates)
        content.addArrangedSubview(output)
    }

    private func buildAPI(into content: NSStackView) {
        let examples = card("Примеры запросов")
        let body = contentStack(examples)
        body.spacing = 10
        body.addArrangedSubview(label("Статус API", size: 12, color: Palette.muted))
        body.addArrangedSubview(label("○  Не подключён", size: 14, weight: .medium, color: Palette.body))
        body.addArrangedSubview(label("http://127.0.0.1:8000  •  адрес примера", size: 14, color: Palette.body))
        body.addArrangedSubview(horizontal([
            button("Скопировать URL", action: #selector(copyAPIURL(_:))),
            button("Открыть документацию", primary: true, action: #selector(openDocumentation(_:))),
            flexibleSpace()
        ], spacing: 12))
        let language = PillSelector(labels: ["Python", "cURL", "JavaScript"], target: self, action: #selector(apiLanguageChanged(_:)))
        language.selectedSegment = ["Python", "cURL", "JavaScript"].firstIndex(of: defaults.string(forKey: "api.exampleLanguage") ?? "Python") ?? 0
        language.heightAnchor.constraint(equalToConstant: 32).isActive = true
        body.addArrangedSubview(language)
        let code = codeView(apiExample(language.selectedSegment))
        apiCodeText = code.documentView as? NSTextView
        code.heightAnchor.constraint(equalToConstant: 330).isActive = true
        body.addArrangedSubview(code)
        body.addArrangedSubview(horizontal([button("Копировать пример", action: #selector(copyCode(_:))), flexibleSpace()], spacing: 12))
        body.addArrangedSubview(wrappedLabel("Пример запроса, не ответ сервера. Этот клиент не запускает API.", size: 12, color: Palette.muted))
        size(examples, width: 570, height: 640)
        let docs = card("Документация")
        for title in ["Быстрый старт", "Эндпоинты", "Параметры", "Примеры", "Форматы ответов", "Скачать OpenAPI (JSON)"] {
            let row = documentationRow(title)
            if title == "Скачать OpenAPI (JSON)" {
                row.isEnabled = false
                row.toolTip = L10n.text("Схема доступна после подключения API.")
            }
            contentStack(docs).addArrangedSubview(row)
        }
        size(docs, width: 292, height: 640)
        content.addArrangedSubview(horizontal([examples, docs], spacing: 16))
    }

    private func buildHistory(into content: NSStackView) {
        let history = card("История")
        let body = contentStack(history)
        body.spacing = 18
        let filters = PillSelector(labels: ["Все", "Успешные", "В обработке", "Ошибки"], target: nil, action: nil)
        filters.selectedSegment = 0
        filters.heightAnchor.constraint(equalToConstant: 32).isActive = true
        filters.isEnabled = false
        filters.toolTip = L10n.text("В истории пока нет записей.")
        let search = NSSearchField()
        search.isBezeled = false
        search.drawsBackground = false
        search.focusRingType = .none
        search.placeholderString = L10n.text("Поиск по истории")
        search.isEnabled = false
        let searchCapsule = insetPanel()
        searchCapsule.layer?.cornerRadius = 19
        search.translatesAutoresizingMaskIntoConstraints = false
        searchCapsule.addSubview(search)
        NSLayoutConstraint.activate([
            search.leadingAnchor.constraint(equalTo: searchCapsule.leadingAnchor, constant: 12),
            search.trailingAnchor.constraint(equalTo: searchCapsule.trailingAnchor, constant: -12),
            search.centerYAnchor.constraint(equalTo: searchCapsule.centerYAnchor)
        ])
        size(searchCapsule, width: 238, height: 38)
        let toolbar = horizontal([filters, flexibleSpace(), searchCapsule], spacing: 12)
        toolbar.alignment = .centerY
        body.addArrangedSubview(toolbar)
        body.addArrangedSubview(divider())
        body.addArrangedSubview(columnHeadings([("Файл", 306), ("Длительность", 140), ("Статус", 134), ("Дата", 180)]))
        body.addArrangedSubview(divider())
        let table = NSTableView()
        table.headerView = nil
        table.backgroundColor = .clear
        table.rowHeight = 56
        table.intercellSpacing = NSSize(width: 12, height: 0)
        table.gridStyleMask = .solidHorizontalGridLineMask
        table.gridColor = Palette.line.withAlphaComponent(0.65)
        for (title, width) in [("Файл", 306.0), ("Длительность", 140.0), ("Статус", 134.0), ("Дата", 180.0)] {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(title))
            column.title = L10n.text(title)
            column.width = width
            table.addTableColumn(column)
        }
        table.setAccessibilityLabel(L10n.text("История обработок: записей нет"))
        let tableScroll = NSScrollView()
        tableScroll.drawsBackground = false
        tableScroll.documentView = table
        tableScroll.heightAnchor.constraint(equalToConstant: 388).isActive = true
        let tableArea = NSView()
        embed(tableScroll, in: tableArea, inset: 0, fillHeight: true)
        let note = wrappedLabel("История пуста. Завершённые обработки появятся здесь.", size: 14, color: Palette.body)
        note.translatesAutoresizingMaskIntoConstraints = false
        tableArea.addSubview(note)
        NSLayoutConstraint.activate([
            note.leadingAnchor.constraint(equalTo: tableArea.leadingAnchor),
            note.trailingAnchor.constraint(equalTo: tableArea.trailingAnchor),
            note.topAnchor.constraint(equalTo: tableArea.topAnchor, constant: 16)
        ])
        body.addArrangedSubview(tableArea)
        body.addArrangedSubview(label("0 записей", size: 12, color: Palette.muted))
        size(history, width: 878, height: 640)
        content.addArrangedSubview(history)
    }

    private func buildSettings(into content: NSStackView) {
        settingsCategoryButtons.removeAll()
        let names = ["Общие", "Модели", "Обработка", "Диаризация", "Аудио", "LLM", "API", "Пути", "О приложении"]
        let stored = defaults.string(forKey: "settings.category") ?? "Общие"
        let selected = names.contains(stored) ? stored : "Общие"
        let categories = GlassView(radius: 18)
        let rail = vertical([], spacing: 8)
        for name in names {
            rail.addArrangedSubview(settingsCategoryButton(name, selected: name == selected))
        }
        embed(rail, in: categories.contentView, inset: 18, top: 30)
        size(categories, width: 260, height: 652)
        let detail = NSView()
        size(detail, width: 602, height: 652)
        settingsDetail = detail
        content.addArrangedSubview(horizontal([categories, detail], spacing: 16))
        applySettingsCategorySelection(selected)
    }

    private func settingsPage(_ category: String) -> NSView {
        let surface = GlassView(radius: 28)
        let body = vertical([label(category, size: 24, weight: .medium, color: Palette.ink)], spacing: 24)
        switch category {
        case "Общие":
            body.addArrangedSubview(wrappedLabel("Язык и оформление приложения. Изменения сохраняются автоматически.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Язык интерфейса", control: popup(["Русский", "English"], key: "settings.language")))
            body.addArrangedSubview(settingsField("Тема", control: popup(["Системная", "Светлая", "Тёмная"], key: "settings.theme")))
        case "Модели":
            body.addArrangedSubview(wrappedLabel("Модель распознавания и устройство вычислений.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.spacing = 16
            body.addArrangedSubview(settingsField("ASR backend", control: popup(["auto", "mlx", "onnx", "pytorch"], key: "settings.backend")))
            body.addArrangedSubview(settingsField("Модель", control: popup(["v3_e2e_rnnt", "multilingual_ctc", "multilingual_large_ctc"], key: "settings.model")))
            body.addArrangedSubview(settingsField("ONNX provider", control: popup(["auto", "cpu", "cuda", "tensorrt", "coreml", "directml"], key: "settings.onnxProvider")))
            body.addArrangedSubview(settingsField("Устройство", control: inactive(popup(["Auto / GPU", "CPU", "GPU"], key: "settings.device"))))
            body.addArrangedSubview(inactive(toggleRow("Fallback на CPU", key: "settings.cpuFallback", defaultValue: false)))
            body.addArrangedSubview(wrappedLabel("Устройство и fallback выбирает backend. ONNX provider применяется только к ONNX. Язык определяется моделью.", size: 12, color: Palette.muted))
        case "Обработка":
            body.addArrangedSubview(wrappedLabel("Разбиение транскрипции и оформление субтитров.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(toggleRow("Разбивать по предложениям", key: "subtitle.sentences", defaultValue: true))
            body.addArrangedSubview(equalColumns([
                settingsField("Строк в блоке", control: popup(["2", "1", "3", "4"], key: "subtitle.lines")),
                settingsField("Символов в строке", control: popup(["64", "42", "80"], key: "subtitle.characters"))
            ], spacing: 20))
            body.addArrangedSubview(inactive(toggleRow("Показывать спикера", key: "settings.showSpeaker", defaultValue: true)))
            body.addArrangedSubview(wrappedLabel("Метки спикеров добавляются автоматически при включённой диаризации.", size: 12, color: Palette.muted))
        case "Диаризация":
            body.addArrangedSubview(wrappedLabel("Разделение речи по спикерам и доступ к моделям Hugging Face.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(toggleRow("Диаризация", key: "settings.diarization", defaultValue: false))
            body.addArrangedSubview(settingsField("Движок диаризации", control: popup(["pyannote", "onnx", "sortformer"], key: "settings.diarizationEngine")))
            body.addArrangedSubview(settingsField("Кол-во спикеров", control: popup(["Авто", "1", "2", "3", "4", "5", "6"], key: "processing.speakers")))
            let value = SecureStore.string(for: "hfToken") ?? ""
            let token = RoundedSecureTextField(string: value)
            token.cell = CenteredSecureTextCell(textCell: value)
            token.isEditable = true
            token.isSelectable = true
            token.isBezeled = false
            token.drawsBackground = false
            token.wantsLayer = true
            token.layer?.cornerRadius = 18
            token.layer?.masksToBounds = true
            token.placeholderString = L10n.text("Не настроен")
            token.identifier = NSUserInterfaceItemIdentifier("settings.hfToken")
            token.target = self
            token.delegate = self
            token.action = #selector(textChanged(_:))
            body.addArrangedSubview(settingsField("HF Token", control: token))
            body.addArrangedSubview(wrappedLabel("Pyannote требует токен и принятые лицензии моделей Hugging Face. Пустое поле использует HF_TOKEN из окружения.", size: 12, color: Palette.muted))
        case "Аудио":
            body.addArrangedSubview(wrappedLabel("Параметры аудиосигнала для обработки.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Подготовка аудио", control: popup(["auto", "off", "light", "denoise"], key: "processing.preprocessing")))
            body.addArrangedSubview(settingsField("Частота дискретизации", control: inactive(popup(["16000 Hz"], key: "settings.sampleRate"))))
            body.addArrangedSubview(wrappedLabel("Частоту 16000 Hz задаёт конвертер. auto — автоматическая подготовка, off — без неё, light — лёгкая обработка, denoise — шумоподавление.", size: 12, color: Palette.muted))
        case "LLM":
            body.addArrangedSubview(wrappedLabel("Сохранённые параметры постобработки. LLM-сервис не подключён.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Провайдер", control: popup(["OpenAI-compatible", "Anthropic", "Локальный"], key: "settings.llmProvider")))
            body.addArrangedSubview(settingsField("Модель", control: editableText(defaults.string(forKey: "settings.llmModel") ?? "gpt-4.1-mini", key: "settings.llmModel", placeholder: "Модель")))
        case "API":
            body.addArrangedSubview(wrappedLabel("Отдельный REST API запускается из api.py. Нативный клиент не запускает сервер и не проверяет его доступность.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(label("Не подключён", size: 17, weight: .medium, color: Palette.body))
            body.addArrangedSubview(settingsField("Адрес в примерах · не настройка подключения", control: label("http://127.0.0.1:8000", size: 15, color: Palette.ink)))
            body.addArrangedSubview(horizontal([button("Скопировать URL", action: #selector(copyAPIURL(_:))), button("Открыть документацию", action: #selector(openDocumentation(_:)))], spacing: 12))
            body.addArrangedSubview(wrappedLabel("Для запросов требуется заголовок X-API-Key. Примеры Python, cURL и JavaScript доступны в разделе API основного меню.", size: 13, color: Palette.body))
        case "Пути":
            body.addArrangedSubview(wrappedLabel("Хранение результатов и визуальные эффекты приложения.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Папка результатов", control: editableText(defaults.string(forKey: "output.path") ?? "~/Documents/GigaAM", key: "output.path", placeholder: "Папка")))
            body.addArrangedSubview(toggleRow("Liquid Glass", key: "settings.liquidGlass", defaultValue: true))
            body.addArrangedSubview(toggleRow("Анимации", key: "settings.animations", defaultValue: true))
        case "О приложении":
            body.spacing = 18
            body.addArrangedSubview(label("GigaAM v3 Transcriber", size: 22, weight: .medium, color: Palette.ink))
            body.addArrangedSubview(wrappedLabel("Транскрибация русской речи из аудио и видео на базе GigaAM-v3.", size: 14, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Релиз основного приложения", control: label("1.3.0", size: 15, color: Palette.ink)))
            body.addArrangedSubview(wrappedLabel("Этот интерфейс — нативный клиент AppKit без отдельного номера релиза. Импорт медиа и распознавание речи используют Python-сервисы проекта. Live и LLM пока не подключены.", size: 13, color: Palette.body))
            body.addArrangedSubview(label("Разработчики приложения", size: 12, color: Palette.muted))
            let developers = ["dubr1k", "Baggrisha"].map { name in
                let link = button(name, action: #selector(openDeveloper(_:)))
                link.identifier = NSUserInterfaceItemIdentifier(name)
                link.toolTip = "https://github.com/\(name)"
                return link
            }
            body.addArrangedSubview(horizontal(developers + [flexibleSpace()], spacing: 12))
            body.addArrangedSubview(settingsField("Модель распознавания", control: label("SaluteDevices / GigaAM", size: 15, color: Palette.ink)))
            body.addArrangedSubview(centered(button("Проект на GitHub", action: #selector(openProject(_:)))))
        default: break
        }
        embed(body, in: surface.contentView, inset: 30)
        return surface
    }

    private func size(_ view: NSView, width: CGFloat, height: CGFloat) {
        view.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            view.widthAnchor.constraint(equalToConstant: width),
            view.heightAnchor.constraint(equalToConstant: height)
        ])
    }

    private func vertical(_ views: [NSView], spacing: CGFloat, alignment: NSLayoutConstraint.Attribute = .width) -> NSStackView {
        let stack = ContentStackView()
        stack.orientation = .vertical
        stack.fillsWidth = alignment == .width
        stack.alignment = alignment == .width ? .leading : alignment
        stack.distribution = .fill
        stack.detachesHiddenViews = false
        stack.spacing = spacing
        stack.setHuggingPriority(.required, for: .vertical)
        stack.setContentHuggingPriority(.required, for: .vertical)
        stack.setContentCompressionResistancePriority(.required, for: .vertical)
        for view in views { stack.addArrangedSubview(view) }
        return stack
    }

    private func equalColumns(_ views: [NSView], spacing: CGFloat) -> NSStackView {
        let stack = horizontal(views, spacing: spacing)
        stack.distribution = .fillEqually
        return stack
    }

    private func flexibleSpace() -> NSView {
        let view = NSView()
        view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return view
    }

    private func centered(_ view: NSView) -> NSView {
        let wrapper = NSView()
        view.translatesAutoresizingMaskIntoConstraints = false
        wrapper.addSubview(view)
        NSLayoutConstraint.activate([
            view.centerXAnchor.constraint(equalTo: wrapper.centerXAnchor),
            view.leadingAnchor.constraint(greaterThanOrEqualTo: wrapper.leadingAnchor),
            view.topAnchor.constraint(equalTo: wrapper.topAnchor),
            view.bottomAnchor.constraint(equalTo: wrapper.bottomAnchor)
        ])
        return wrapper
    }

    private func embed(_ child: NSView, in parent: NSView, inset: CGFloat, top: CGFloat? = nil, fillHeight: Bool = false) {
        child.translatesAutoresizingMaskIntoConstraints = false
        parent.addSubview(child)
        NSLayoutConstraint.activate([
            child.leadingAnchor.constraint(equalTo: parent.leadingAnchor, constant: inset),
            child.trailingAnchor.constraint(equalTo: parent.trailingAnchor, constant: -inset),
            child.topAnchor.constraint(equalTo: parent.topAnchor, constant: top ?? inset),
            fillHeight
                ? child.bottomAnchor.constraint(equalTo: parent.bottomAnchor, constant: -inset)
                : child.bottomAnchor.constraint(lessThanOrEqualTo: parent.bottomAnchor, constant: -inset)
        ])
    }

    private func insetPanel() -> NSView {
        let view = NSView()
        view.wantsLayer = true
        view.layer?.cornerRadius = 12
        view.layer?.borderWidth = 0.5
        view.layer?.borderColor = Palette.line.withAlphaComponent(0.75).cgColor
        view.layer?.backgroundColor = NSColor.white.withAlphaComponent(Palette.isDark ? 0.025 : 0.24).cgColor
        return view
    }

    private func wrappedLabel(_ text: String, size: CGFloat, color: NSColor) -> NSTextField {
        let field = label(text, size: size, color: color)
        field.maximumNumberOfLines = 0
        field.lineBreakMode = .byWordWrapping
        return field
    }

    private func columnHeadings(_ columns: [(String, CGFloat)]) -> NSView {
        let labels = columns.map { title, width -> NSView in
            let text = label(title, size: 12, color: Palette.muted)
            text.widthAnchor.constraint(equalToConstant: width).isActive = true
            return text
        }
        return horizontal(labels + [flexibleSpace()], spacing: 8)
    }

    private func symbol(_ name: String, size: CGFloat) -> NSImageView {
        let image = NSImageView()
        image.image = NSImage(systemSymbolName: name, accessibilityDescription: nil)
        image.contentTintColor = Palette.blue
        self.size(image, width: size, height: size)
        return image
    }

    private func unavailableButton(_ title: String, primary: Bool = false, reason: String = "Нет данных: рабочий сервис не подключён.", height: CGFloat = 38) -> NSButton {
        let control = button(title, primary: primary, height: height)
        control.isEnabled = false
        control.toolTip = L10n.text(reason)
        if primary {
            control.layer?.backgroundColor = (Palette.isDark ? NSColor.white : NSColor.black).cgColor
            control.contentTintColor = Palette.isDark ? .black : .white
        }
        return control
    }

    private func unavailableIcon(_ name: String, hint: String) -> NSButton {
        let control = iconButton(name, hint: hint)
        control.isEnabled = false
        return control
    }

    private func textEditor(_ value: String, key: String?, height: CGFloat?) -> NSScrollView {
        let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 280, height: height ?? 330))
        text.string = value
        text.isRichText = false
        text.font = NSFont.systemFont(ofSize: 13)
        text.textColor = Palette.ink
        text.drawsBackground = false
        text.textContainerInset = NSSize(width: 12, height: 12)
        text.isVerticallyResizable = true
        text.isHorizontallyResizable = false
        text.autoresizingMask = [.width]
        text.minSize = .zero
        text.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        text.textContainer?.widthTracksTextView = true
        text.textContainer?.containerSize = NSSize(width: 280, height: CGFloat.greatestFiniteMagnitude)
        if let key {
            text.identifier = NSUserInterfaceItemIdentifier(key)
            text.delegate = self
        }
        let scroll = EditorScrollView()
        scroll.drawsBackground = true
        scroll.documentView = text
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.wantsLayer = true
        scroll.layer?.cornerRadius = 12
        scroll.layer?.masksToBounds = true
        scroll.layer?.borderWidth = 1
        scroll.layer?.borderColor = (Palette.isDark ? NSColor.white.withAlphaComponent(0.25) : NSColor.black.withAlphaComponent(0.16)).cgColor
        scroll.backgroundColor = Palette.isDark ? NSColor(calibratedWhite: 0.08, alpha: 0.72) : NSColor.white.withAlphaComponent(0.72)
        scroll.contentView.drawsBackground = true
        scroll.contentView.backgroundColor = scroll.backgroundColor
        if let height { scroll.heightAnchor.constraint(equalToConstant: height).isActive = true }
        return scroll
    }

    private func apiExample(_ language: Int) -> String {
        switch language {
        case 1:
            return """
            # Пример: задайте GIGAAM_API_KEY и путь к файлу
            curl -X POST \\
              "http://127.0.0.1:8000/api/v1/transcribe" \\
              -H "X-API-Key: $GIGAAM_API_KEY" \\
              -F "file=@audio.mp3"
            """
        case 2:
            return """
            // Пример для Node.js; задайте GIGAAM_API_KEY
            import { readFile } from "node:fs/promises";

            const data = new FormData();
            data.append("file",
              new Blob([await readFile("audio.mp3")]),
              "audio.mp3");
            const response = await fetch(
              "http://127.0.0.1:8000/api/v1/transcribe",
              { method: "POST", body: data, headers: {
                "X-API-Key": process.env.GIGAAM_API_KEY
              }});
            console.log(await response.json());
            """
        default:
            return """
            # Пример: задайте GIGAAM_API_KEY и путь к файлу
            import os
            import requests

            url = "http://127.0.0.1:8000/api/v1/transcribe"
            headers = {"X-API-Key": os.environ["GIGAAM_API_KEY"]}
            with open("audio.mp3", "rb") as audio:
                response = requests.post(
                    url, headers=headers,
                    files={"file": audio}, timeout=120)
            response.raise_for_status()
            print(response.json())
            """
        }
    }

    // MARK: - Native controls
    private func card(_ title: String, trailing: NSView? = nil, dense: Bool = false) -> GlassView {
        let view = GlassView(radius: dense ? 14 : 16)
        view.translatesAutoresizingMaskIntoConstraints = false
        let titleLabel = label(title, size: dense ? 14 : 17, weight: .medium, color: Palette.ink)
        let header = horizontal([titleLabel, flexibleSpace()], spacing: 8)
        header.alignment = .centerY
        if let trailing { header.addArrangedSubview(trailing) }
        let body = vertical([], spacing: dense ? 6 : 12)
        body.identifier = NSUserInterfaceItemIdentifier("card.body")
        header.translatesAutoresizingMaskIntoConstraints = false
        body.translatesAutoresizingMaskIntoConstraints = false
        view.contentView.addSubview(header)
        view.contentView.addSubview(body)
        let inset: CGFloat = dense ? 16 : 18
        NSLayoutConstraint.activate([
            header.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: inset),
            header.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -inset),
            header.topAnchor.constraint(equalTo: view.topAnchor, constant: dense ? 12 : 14),
            body.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: inset),
            body.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -inset),
            body.topAnchor.constraint(equalTo: header.bottomAnchor, constant: dense ? 6 : 10),
            body.bottomAnchor.constraint(lessThanOrEqualTo: view.bottomAnchor, constant: dense ? -12 : -16)
        ])
        return view
    }

    private func compactCard(_ title: String) -> GlassView {
        card(title, dense: true)
    }

    private func contentStack(_ card: GlassView) -> NSStackView {
        guard let stack = card.contentView.subviews.compactMap({ $0 as? NSStackView }).first(where: { $0.identifier?.rawValue == "card.body" }) else { fatalError("Card body missing") }
        return stack
    }


    private func compactField(_ title: String, control: NSView) -> NSView {
        let stack = vertical([label(title, size: 11, color: Palette.muted), control], spacing: 4)
        control.heightAnchor.constraint(equalToConstant: 28).isActive = true
        control.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        control.setAccessibilityLabel(L10n.text(title))
        return stack
    }

    private func settingsField(_ title: String, control: NSView) -> NSView {
        let stack = vertical([label(title, size: 12, color: Palette.muted), control], spacing: 8)
        control.heightAnchor.constraint(equalToConstant: 36).isActive = true
        control.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        control.setAccessibilityLabel(L10n.text(title))
        if let field = control as? NSTextField {
            field.font = NSFont.systemFont(ofSize: 13)
            field.lineBreakMode = .byTruncatingMiddle
        }
        return stack
    }

    private func inactive(_ view: NSView) -> NSView {
        if let control = view as? NSControl { control.isEnabled = false }
        view.subviews.forEach { _ = inactive($0) }
        view.toolTip = L10n.text("Параметр задаёт рабочий сервис; отдельное управление недоступно.")
        return view
    }

    private func popup(_ values: [String], key: String) -> NSPopUpButton {
        let popup = GlassPopupButton()
        popup.cell = GlassPopupCell(textCell: "", pullsDown: false)
        popup.addItems(withTitles: values)
        let stored = defaults.string(forKey: key)
        if let stored, values.contains(stored) { popup.selectItem(withTitle: stored) }
        popup.target = self
        popup.action = #selector(popupChanged(_:))
        popup.identifier = NSUserInterfaceItemIdentifier(key)
        popup.controlSize = .small
        popup.font = NSFont.systemFont(ofSize: 13)
        popup.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return popup
    }

    private func editableText(_ value: String, key: String, placeholder: String) -> NSTextField {
        let field = RoundedTextField(string: value)
        field.cell = CenteredTextCell(textCell: value)
        field.isEditable = true
        field.isSelectable = true
        field.drawsBackground = false
        field.isBezeled = false
        field.wantsLayer = true
        field.layer?.cornerRadius = 16
        field.layer?.masksToBounds = true
        field.placeholderString = L10n.text(placeholder)
        field.font = NSFont.systemFont(ofSize: 14)
        field.identifier = NSUserInterfaceItemIdentifier(key)
        field.target = self
        field.delegate = self
        field.action = #selector(textChanged(_:))
        return field
    }

    private func toggleRow(_ title: String, key: String, defaultValue: Bool) -> NSView {
        let text = wrappedLabel(title, size: 12, color: Palette.body)
        text.maximumNumberOfLines = 2
        let toggle = ThemedSwitch()
        toggle.controlSize = .small
        toggle.state = defaults.object(forKey: key) == nil ? (defaultValue ? .on : .off) : (defaults.bool(forKey: key) ? .on : .off)
        toggle.identifier = NSUserInterfaceItemIdentifier(key)
        toggle.target = self
        toggle.action = #selector(switchChanged(_:))
        toggle.setAccessibilityLabel(L10n.text(title))
        toggle.setContentCompressionResistancePriority(.required, for: .horizontal)
        let row = horizontal([text, flexibleSpace(), toggle], spacing: 6)
        row.alignment = .centerY
        row.heightAnchor.constraint(greaterThanOrEqualToConstant: 24).isActive = true
        return row
    }

    private func progressCard() -> GlassView {
        let view = GlassView(radius: 16)
        let progress = ProgressTrackView()
        progress.heightAnchor.constraint(equalToConstant: 8).isActive = true
        progress.setContentHuggingPriority(.defaultLow, for: .horizontal)
        progressTrack = progress
        let percentage = label("", size: 12, color: Palette.muted)
        percentage.identifier = NSUserInterfaceItemIdentifier("transcription.progress")
        progressPercentage = percentage
        let log = button("Журнал обработки", action: #selector(showProcessingLog(_:)), height: 30)
        log.identifier = NSUserInterfaceItemIdentifier("transcription.log")
        let row = horizontal([label("Общий прогресс", size: 14, weight: .medium, color: Palette.ink), progress, percentage, log], spacing: 16)
        row.alignment = .centerY
        let status = wrappedLabel("", size: 12, color: Palette.body)
        status.maximumNumberOfLines = 3
        status.identifier = NSUserInterfaceItemIdentifier("transcription.status")
        progressStatus = status
        let body = vertical([row, status], spacing: 8)
        embed(body, in: view.contentView, inset: 20)
        view.widthAnchor.constraint(equalToConstant: 878).isActive = true
        body.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -20).isActive = true
        refreshProgress()
        return view
    }

    private func checkbox(_ title: String, key: String, defaultValue: Bool) -> NSButton {
        let box = RoundedCheckButton(checkboxWithTitle: L10n.text(title), target: self, action: #selector(switchChanged(_:)))

        box.identifier = NSUserInterfaceItemIdentifier(key)
        box.state = defaults.object(forKey: key) == nil ? (defaultValue ? .on : .off) : (defaults.bool(forKey: key) ? .on : .off)
        box.font = NSFont.systemFont(ofSize: 12)
        box.controlSize = .small
        box.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        box.heightAnchor.constraint(equalToConstant: 18).isActive = true
        return box
    }

    private func template(_ title: String, _ detail: String) -> NSButton {
        let button = PaddedButton(title: L10n.text(title), target: self, action: #selector(selectTemplate(_:)))
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .left
        paragraph.lineBreakMode = .byWordWrapping
        let text = NSMutableAttributedString(string: L10n.text(title) + "\n", attributes: [
            .font: NSFont.systemFont(ofSize: 12, weight: .medium), .foregroundColor: Palette.ink, .paragraphStyle: paragraph
        ])
        text.append(NSAttributedString(string: L10n.text(detail), attributes: [
            .font: NSFont.systemFont(ofSize: 11), .foregroundColor: Palette.body, .paragraphStyle: paragraph
        ]))
        button.attributedTitle = text
        button.bezelStyle = .regularSquare
        button.isBordered = false
        button.wantsLayer = true
        button.layer?.cornerRadius = 12
        button.layer?.borderWidth = 0.5
        button.layer?.borderColor = Palette.line.cgColor
        button.layer?.backgroundColor = NSColor.white.withAlphaComponent(Palette.isDark ? 0.04 : 0.26).cgColor
        button.alignment = .left
        button.identifier = NSUserInterfaceItemIdentifier(title)
        button.toolTip = L10n.text(detail)
        button.heightAnchor.constraint(equalToConstant: 64).isActive = true
        return button
    }

    private func documentationRow(_ title: String) -> NSButton {
        let button = button(title, action: #selector(openDocumentation(_:)), height: 46)
        button.identifier = NSUserInterfaceItemIdentifier(title)
        button.alignment = .left
        button.image = NSImage(systemSymbolName: "chevron.right", accessibilityDescription: nil)
        button.imagePosition = .imageTrailing
        button.font = NSFont.systemFont(ofSize: 14)
        return button
    }

    private func settingsCategoryButton(_ title: String, selected: Bool) -> NSButton {
        let button = NavigationRowButton(title: L10n.text(title), target: self, action: #selector(selectSettingsCategory(_:)))
        button.identifier = NSUserInterfaceItemIdentifier("settings.category.\(title)")
        button.isBordered = false
        button.bezelStyle = .regularSquare
        button.alignment = .left
        button.font = NSFont.systemFont(ofSize: 14, weight: .medium)
        button.wantsLayer = true
        button.layer?.cornerRadius = 9
        button.widthAnchor.constraint(equalToConstant: 224).isActive = true
        button.heightAnchor.constraint(equalToConstant: 38).isActive = true
        settingsCategoryButtons[title] = button
        button.contentTintColor = selected ? Palette.ink : Palette.body
        button.layer?.backgroundColor = (selected ? Palette.selection : .clear).cgColor
        return button
    }

    private func applySettingsCategorySelection(_ selectedTitle: String) {
        guard let settingsDetail, settingsCategoryButtons[selectedTitle] != nil else { return }
        window.makeFirstResponder(nil)
        defaults.set(selectedTitle, forKey: "settings.category")
        for (title, button) in settingsCategoryButtons {
            let isSelected = title == selectedTitle
            button.contentTintColor = isSelected ? Palette.blue : Palette.body
            button.layer?.backgroundColor = (isSelected ? Palette.selection : .clear).cgColor
        }
        settingsDetail.subviews.forEach { $0.removeFromSuperview() }
        embed(settingsPage(selectedTitle), in: settingsDetail, inset: 0, fillHeight: true)
    }


    private func codeView(_ code: String) -> NSScrollView {
        let scroll = textEditor(code, key: nil, height: nil)
        guard let text = scroll.documentView as? NSTextView else { return scroll }
        text.isEditable = false
        text.font = NSFont.monospacedSystemFont(ofSize: 12.5, weight: .regular)
        text.textColor = NSColor(calibratedWhite: 0.94, alpha: 1)
        text.backgroundColor = NSColor(calibratedWhite: 0.07, alpha: 0.92)
        text.drawsBackground = true
        scroll.drawsBackground = true
        scroll.backgroundColor = text.backgroundColor
        return scroll
    }



    private func label(_ text: String, size: CGFloat, weight: NSFont.Weight = .regular, color: NSColor) -> NSTextField {
        let label = NSTextField(wrappingLabelWithString: L10n.text(text))
        label.font = NSFont.systemFont(ofSize: size, weight: weight)
        label.textColor = color
        label.maximumNumberOfLines = 1
        label.lineBreakMode = .byTruncatingTail
        label.setContentCompressionResistancePriority(.required, for: .vertical)
        label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return label
    }

    private func horizontal(_ views: [NSView], spacing: CGFloat) -> NSStackView {
        for view in views { view.translatesAutoresizingMaskIntoConstraints = false }
        let stack = NSStackView(views: views)
        stack.orientation = .horizontal
        stack.alignment = .top
        stack.distribution = .fill
        stack.detachesHiddenViews = false
        stack.setContentHuggingPriority(.required, for: .vertical)
        stack.setContentCompressionResistancePriority(.required, for: .vertical)
        stack.spacing = spacing
        return stack
    }

    private func divider() -> NSBox {
        let box = NSBox()
        box.boxType = .separator
        return box
    }

    private func button(_ title: String, primary: Bool = false, action: Selector? = nil, height: CGFloat = 38) -> NSButton {
        let button = PaddedButton(title: L10n.text(title), target: self, action: action)
        button.font = NSFont.systemFont(ofSize: 14, weight: .regular)
        button.contentTintColor = primary ? (Palette.isDark ? .black : .white) : Palette.ink
        button.wantsLayer = true
        button.layer?.cornerRadius = height / 2
        if primary {
            button.isBordered = false
            button.bezelStyle = .regularSquare
            button.layer?.backgroundColor = Palette.primary.cgColor
        } else {
            button.isBordered = false
            button.bezelStyle = .regularSquare
            button.layer?.backgroundColor = NSColor.white.withAlphaComponent(Palette.isDark ? 0.06 : 0.42).cgColor
            button.layer?.borderWidth = 0.5
            button.layer?.borderColor = Palette.line.cgColor
        }
        button.heightAnchor.constraint(equalToConstant: height).isActive = true
        button.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return button
    }

    private func pill(_ title: String, width: CGFloat, action: Selector?) -> NSButton {
        let button = button(title, action: action)
        button.widthAnchor.constraint(equalToConstant: width).isActive = true
        return button
    }

    private func iconButton(_ name: String, hint: String, action: Selector? = nil) -> NSButton {
        let button = CircularIconButton(image: NSImage(systemSymbolName: name, accessibilityDescription: L10n.text(hint)) ?? NSImage(), target: self, action: action)
        button.isBordered = false
        button.setAccessibilityLabel(L10n.text(hint))
        button.toolTip = L10n.text(hint)
        button.contentTintColor = Palette.ink
        button.imageScaling = .scaleProportionallyDown
        button.widthAnchor.constraint(equalToConstant: 38).isActive = true
        button.heightAnchor.constraint(equalToConstant: 38).isActive = true
        return button
    }

    // MARK: - Interaction and persistence

    private var currentResult: NativeTranscriptionResult? {
        transcriptionResults.first { $0.inputURL == selectedResultURL } ?? transcriptionResults.first
    }

    private var existingOutputs: [String: URL] {
        (currentResult?.outputFiles ?? [:]).filter { FileManager.default.isReadableFile(atPath: $0.value.path) }
    }

    private func option(_ key: String, values: [String]) -> String {
        let stored = defaults.string(forKey: key) ?? values[0]
        return values.contains(stored) ? stored : values[0]
    }

    private func enabledOption(_ key: String, defaultValue: Bool) -> Bool {
        defaults.object(forKey: key) == nil ? defaultValue : defaults.bool(forKey: key)
    }

    private var outputFormats: [String] {
        [("output.txt", "txt", true), ("output.timestamps", "txt_timecodes", true), ("output.md", "md", false), ("output.srt", "srt", false), ("output.vtt", "vtt", false)].compactMap { key, format, fallback in
            enabledOption(key, defaultValue: fallback) ? format : nil
        }
    }

    private func transcriptionSettings() -> NativeTranscriptionSettings {
        var settings = NativeTranscriptionSettings()
        settings.formats = outputFormats
        settings.backend = option("settings.backend", values: ["auto", "mlx", "onnx", "pytorch"])
        settings.model = option("settings.model", values: ["v3_e2e_rnnt", "multilingual_ctc", "multilingual_large_ctc"])
        settings.onnxProvider = option("settings.onnxProvider", values: ["auto", "cpu", "cuda", "tensorrt", "coreml", "directml"])
        settings.diarization = enabledOption("settings.diarization", defaultValue: false)
        settings.diarizationBackend = option("settings.diarizationEngine", values: ["pyannote", "onnx", "sortformer"])
        settings.numSpeakers = settings.diarization ? Int(option("processing.speakers", values: ["Авто", "1", "2", "3", "4", "5", "6"])) : nil
        settings.audioPreprocessingMode = option("processing.preprocessing", values: ["auto", "off", "light", "denoise"])
        settings.subtitleSentenceSplit = enabledOption("subtitle.sentences", defaultValue: true)
        settings.subtitleMaxLines = Int(option("subtitle.lines", values: ["2", "1", "3", "4"])) ?? 2
        settings.subtitleMaxWidth = Int(option("subtitle.characters", values: ["64", "42", "80"])) ?? 64
        let token = (SecureStore.string(for: "hfToken") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        settings.hfToken = token.isEmpty ? nil : token
        return settings
    }

    private var outputDirectory: URL? {
        let raw = (defaults.string(forKey: "output.path") ?? "~/Documents/GigaAM").trimmingCharacters(in: .whitespacesAndNewlines)
        let path = (raw as NSString).expandingTildeInPath
        guard path.hasPrefix("/"), !path.contains("\0") else { return nil }
        let destination = URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL
        var ancestor = destination
        var directory: ObjCBool = false
        while !FileManager.default.fileExists(atPath: ancestor.path, isDirectory: &directory) {
            let parent = ancestor.deletingLastPathComponent().standardizedFileURL
            guard parent != ancestor else { return nil }
            ancestor = parent
        }
        return directory.boolValue && FileManager.default.isWritableFile(atPath: ancestor.path) ? destination : nil
    }

    private func refreshProcessingControls() {
        let busy = transcriptionJob != nil || mediaDownloadJob != nil || isClosing
        func update(_ view: NSView) {
            if let control = view as? NSControl, let key = control.identifier?.rawValue {
                if key.hasPrefix("processing.") || key.hasPrefix("output.") || key.hasPrefix("subtitle.") || ["settings.backend", "settings.model", "settings.onnxProvider", "settings.diarization", "settings.diarizationEngine", "settings.hfToken"].contains(key) {
                    control.isEnabled = !busy
                    if key == "processing.speakers" || key == "settings.diarizationEngine" { control.isEnabled = !busy && enabledOption("settings.diarization", defaultValue: false) }
                }
            }
            view.subviews.forEach(update)
        }
        if let root = window.contentView { update(root) }
        let reason: String
        if transcriptionJob != nil { reason = "Настройки зафиксированы до завершения обработки. Навигация доступна." }
        else if mediaDownloadJob != nil { reason = "Дождитесь завершения импорта медиа." }
        else if selectedFileURLs.isEmpty { reason = "Добавьте аудио или видео для начала обработки." }
        else if outputDirectory == nil { reason = "Укажите абсолютный путь к доступной папке результатов." }
        else if outputFormats.isEmpty { reason = "Выберите хотя бы один формат вывода." }
        else { reason = "" }
        startProcessingButton?.isEnabled = !busy && reason.isEmpty
        startProcessingButton?.toolTip = reason.isEmpty ? nil : L10n.text(reason)
        processingValidationLabel?.stringValue = L10n.text(reason)
        processingValidationLabel?.isHidden = reason.isEmpty
        cancelProcessingButton?.isEnabled = transcriptionJob != nil && !cancellationRequested && !isClosing
        cancelProcessingButton?.title = L10n.text(cancellationRequested ? "Остановка запрошена" : "Остановить после текущего файла")
    }

    private func refreshProgress() {
        progressTrack?.fraction = transcriptionProgress ?? 0
        progressPercentage?.stringValue = transcriptionProgress.map { "\(Int($0 * 100))%" } ?? "—"
        let status = cancellationRequested && transcriptionJob != nil ? L10n.text("Остановка после текущего файла.") + " " + L10n.text(transcriptionStatus) : L10n.text(transcriptionStatus)
        progressStatus?.stringValue = status
        progressStatus?.toolTip = status
        progressStatus?.invalidateIntrinsicContentSize()
    }

    @objc private func startProcessing(_ sender: Any?) {
        window.makeFirstResponder(nil)
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil else { return }
        let settings = transcriptionSettings()
        guard !selectedFileURLs.isEmpty, !settings.formats.isEmpty, let destination = outputDirectory else {
            showNotice("Не удалось начать обработку", "Выберите файлы, доступную папку и хотя бы один формат.")
            return
        }
        let groupedStems = Dictionary(grouping: selectedFileURLs) {
            $0.deletingPathExtension().lastPathComponent.folding(
                options: [.caseInsensitive, .diacriticInsensitive], locale: .current
            )
        }
        let collisions = groupedStems.values.filter { $0.count > 1 }
        if !collisions.isEmpty {
            let names = collisions.flatMap { $0.map(\.lastPathComponent) }.sorted().joined(separator: ", ")
            showNotice(
                "Не удалось начать обработку",
                "Файлы с одинаковым базовым именем перезапишут результаты друг друга: \(names). Переименуйте файлы или обработайте их отдельно."
            )
            return
        }
        do {
            try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
        } catch {
            showNotice("Не удалось начать обработку", error.localizedDescription)
            return
        }
        transcriptionFiles = selectedFileURLs.map(\.standardizedFileURL)
        for file in transcriptionFiles { fileStates[file] = "В очереди" }
        cancellationRequested = false
        transcriptionProgress = nil
        transcriptionStatus = "Запуск распознавания…"
        transcriptionLog = ""
        let job = NativeTranscriptionJob(files: transcriptionFiles, outputDirectory: destination, settings: settings) { [weak self] event in
            self?.receiveTranscriptionEvent(event)
        }
        transcriptionJob = job
        refreshSelectedFiles()
        refreshProgress()
        job.start()
    }

    @objc private func cancelProcessing(_ sender: Any?) {
        guard let job = transcriptionJob, !cancellationRequested else { return }
        cancellationRequested = true
        job.cancel()
        refreshProcessingControls()
        refreshProgress()
    }

    private func receiveTranscriptionEvent(_ event: NativeTranscriptionEvent) {
        switch event {
        case .log(let message):
            transcriptionLog += message + "\n"
            if transcriptionLog.utf8.count > 131_072 { transcriptionLog = String(transcriptionLog.suffix(65_536)) }
        case .fileStarted(let file, let index, let total):
            fileStates[file.standardizedFileURL] = "В обработке"
            transcriptionStatus = "\(index + 1)/\(total) · \(file.lastPathComponent)"
            refreshSelectedFiles()
            refreshProgress()
        case .progress(let value, let message):
            transcriptionProgress = value
            if !message.isEmpty { transcriptionStatus = message }
            refreshProgress()
        case .fileCompleted(let result):
            fileStates[result.inputURL.standardizedFileURL] = result.error == nil ? "Готово" : "Ошибка"
            if let index = transcriptionResults.firstIndex(where: { $0.inputURL == result.inputURL }) { transcriptionResults[index] = result }
            else { transcriptionResults.append(result) }
            if selectedResultURL == nil { selectedResultURL = result.inputURL }
            if let error = result.error { transcriptionLog += "\(result.inputURL.lastPathComponent): \(error)\n" }
            refreshSelectedFiles()
            if !isClosing, currentPage == .result { show(page: .result) }
        case .completed(let success, let cancelled):
            finishTranscription(status: cancelled ? "Обработка остановлена. Готовые результаты сохранены." : (success ? "Обработка завершена. Результаты доступны в разделе «Результат»." : "Обработка завершена с ошибками. Подробности — в журнале обработки."), pendingState: cancelled ? "Не обработан: остановлено" : "Не обработан")
            if success && !cancelled { transcriptionProgress = 1 }
            refreshProgress()
        case .failed(let message):
            transcriptionLog += message + "\n"
            finishTranscription(status: message, pendingState: "Не обработан: ошибка")
        }
    }

    private func finishTranscription(status: String, pendingState: String) {
        transcriptionJob = nil
        transcriptionStatus = status
        for file in transcriptionFiles where fileStates[file] == "В очереди" || fileStates[file] == "В обработке" { fileStates[file] = pendingState }
        if isTerminating { replyWhenJobsFinished(); return }
        guard !isClosing else { return }
        refreshSelectedFiles()
        refreshProgress()
    }

    private func replyWhenJobsFinished() {
        if isTerminating && transcriptionJob == nil && mediaDownloadJob == nil { NSApp.reply(toApplicationShouldTerminate: true) }
    }

    @objc private func showProcessingLog(_ sender: Any?) {
        let alert = NSAlert()
        alert.messageText = L10n.text("Журнал обработки")
        alert.addButton(withTitle: L10n.text("Понятно"))
        let editor = textEditor(transcriptionLog.isEmpty ? L10n.text(transcriptionStatus) : transcriptionLog, key: nil, height: 300)
        editor.frame = NSRect(x: 0, y: 0, width: 650, height: 300)
        (editor.documentView as? NSTextView)?.isEditable = false
        alert.accessoryView = editor
        alert.beginSheetModal(for: window)
    }

    @objc private func popupChanged(_ sender: NSPopUpButton) {
        guard let key = sender.identifier?.rawValue, let value = sender.titleOfSelectedItem else { return }
        defaults.set(value, forKey: key)
        if key == "settings.theme" || key == "settings.language" { rebuildInterface() }
        refreshProcessingControls()
    }

    @objc private func switchChanged(_ sender: NSButton) {
        guard let key = sender.identifier?.rawValue else { return }
        defaults.set(sender.state == .on, forKey: key)
        if key == "settings.liquidGlass" { rebuildInterface() }
        refreshProcessingControls()
    }

    @objc private func textChanged(_ sender: NSTextField) {
        guard let key = sender.identifier?.rawValue else { return }
        if key == "settings.hfToken" {
            do {
                try SecureStore.set(sender.stringValue.trimmingCharacters(in: .whitespacesAndNewlines), for: "hfToken")
            } catch {
                showNotice("Не удалось сохранить HF Token", error.localizedDescription)
            }
        } else {
            defaults.set(sender.stringValue, forKey: key)
        }
        if key == "output.path" { refreshProcessingControls() }
    }

    @objc private func chooseFiles(_ sender: Any?) {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil else { return }
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowedContentTypes = [.audio, .movie]
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let self else { return }
            guard self.transcriptionJob == nil, !self.isClosing else { return }
            var known = Set(self.selectedFileURLs.map { $0.standardizedFileURL.resolvingSymlinksInPath() })
            self.selectedFileURLs.append(contentsOf: panel.urls.map { $0.standardizedFileURL.resolvingSymlinksInPath() }.filter { known.insert($0).inserted })
            self.refreshSelectedFiles()
        }
    }

    @objc private func chooseMediaURL(_ sender: Any?) {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil, window.attachedSheet == nil else { return }
        presentMediaURLSheet()
    }

    private func presentMediaURLSheet(value: String = "", error: String? = nil) {
        guard !isClosing else { return }
        let alert = NSAlert()
        alert.messageText = L10n.text("Ссылка на медиа")
        alert.informativeText = L10n.text("Вставьте ссылку http:// или https://. Медиа будет загружено в кэш приложения и добавлено к выбранным файлам.")
        alert.addButton(withTitle: L10n.text("Загрузить"))
        alert.addButton(withTitle: L10n.text("Отмена"))
        let input = NSTextField(string: value)
        input.placeholderString = "https://…"
        input.identifier = NSUserInterfaceItemIdentifier("media.url")
        input.setAccessibilityLabel(L10n.text("Ссылка на медиа"))
        let accessory = NSStackView(views: [input])
        accessory.orientation = .vertical
        accessory.alignment = .leading
        accessory.spacing = 12
        input.widthAnchor.constraint(equalToConstant: 440).isActive = true
        input.heightAnchor.constraint(equalToConstant: 28).isActive = true
        if let error {
            let details = NSTextView()
            details.isEditable = false
            details.isRichText = false
            details.font = .systemFont(ofSize: 12)
            details.textColor = .labelColor
            details.string = error
            details.textContainer?.widthTracksTextView = true
            details.textContainerInset = NSSize(width: 6, height: 6)
            let scroll = NSScrollView()
            scroll.hasVerticalScroller = true
            scroll.borderType = .bezelBorder
            scroll.documentView = details
            details.frame = NSRect(x: 0, y: 0, width: 424, height: 130)
            details.isVerticallyResizable = true
            details.autoresizingMask = [.width]
            scroll.widthAnchor.constraint(equalToConstant: 440).isActive = true
            scroll.heightAnchor.constraint(equalToConstant: 130).isActive = true
            accessory.addArrangedSubview(scroll)
        }
        accessory.frame.size = NSSize(width: 440, height: error == nil ? 28 : 170)
        alert.accessoryView = accessory
        mediaImportAlert = alert
        alert.beginSheetModal(for: window) { [weak self, weak alert] response in
            guard let self, let alert, !self.isClosing, self.mediaImportAlert === alert else { return }
            self.mediaImportAlert = nil
            guard response == .alertFirstButtonReturn else { return }
            guard let url = MediaDownloadJob.validatedURL(input.stringValue) else {
                self.presentMediaURLSheet(value: input.stringValue, error: L10n.text("Введите корректную ссылку http:// или https:// с именем сервера."))
                return
            }
            self.downloadMedia(url)
        }
        alert.window.makeFirstResponder(input)
    }

    private func downloadMedia(_ url: URL) {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil else { return }
        let alert = NSAlert()
        alert.messageText = L10n.text("Загрузка медиа")
        alert.informativeText = L10n.text("Загрузчик работает. Точный прогресс недоступен. После завершения файл появится в списке выбранных.")
        alert.addButton(withTitle: L10n.text("Отмена"))
        let spinner = NSProgressIndicator(frame: NSRect(x: 0, y: 0, width: 28, height: 28))
        spinner.style = .spinning
        spinner.isIndeterminate = true
        spinner.startAnimation(nil)
        alert.accessoryView = spinner
        mediaImportAlert = alert
        let job = MediaDownloadJob(url: url) { [weak self, weak alert] result in
            guard let self else { return }
            self.mediaDownloadJob = nil
            if self.isTerminating { self.replyWhenJobsFinished(); return }
            guard !self.isClosing else { return }
            self.refreshProcessingControls()
            if let alert, self.mediaImportAlert === alert {
                self.mediaImportAlert = nil
                self.window.endSheet(alert.window, returnCode: .abort)
                alert.window.orderOut(nil)
            }
            switch result {
            case .success(let files):
                self.rememberDownloadedMedia(files)
                var known = Set(self.selectedFileURLs.map { $0.standardizedFileURL.resolvingSymlinksInPath() })
                self.selectedFileURLs.append(contentsOf: files.filter { known.insert($0).inserted })
                self.refreshSelectedFiles()
            case .failure(let error):
                if let failure = error as? MediaDownloadJob.Failure, case .cancelled = failure { return }
                self.presentMediaURLSheet(value: url.absoluteString, error: error.localizedDescription)
            }
        }
        mediaDownloadJob = job
        refreshProcessingControls()
        alert.beginSheetModal(for: window) { [weak self, weak alert] response in
            guard let self, let alert, self.mediaImportAlert === alert else { return }
            self.mediaImportAlert = nil
            if response == .alertFirstButtonReturn { self.mediaDownloadJob?.cancel() }
        }
        job.start()
    }

    func windowWillClose(_ notification: Notification) {
        isClosing = true
        mediaImportAlert = nil
        mediaDownloadJob?.cancel()
        transcriptionJob?.cancel()
        transcriptionJob?.terminate()
        cleanupDownloadedMedia()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        isClosing = true
        guard mediaDownloadJob != nil || transcriptionJob != nil else { return .terminateNow }
        isTerminating = true
        mediaDownloadJob?.cancel()
        transcriptionJob?.cancel()
        transcriptionJob?.terminate()
        return .terminateLater
    }

    @objc private func clearFiles(_ sender: Any?) {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil else { return }
        selectedFileURLs.removeAll()
        cleanupDownloadedMedia()
        refreshSelectedFiles()
    }

    private func mediaCacheRoot() -> URL? {
        try? FileManager.default.url(
            for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        ).appendingPathComponent("GigaAMLiquid/Media", isDirectory: true).standardizedFileURL
    }

    private func rememberDownloadedMedia(_ files: [URL]) {
        guard let cache = mediaCacheRoot() else { return }
        let prefix = cache.path + "/"
        for file in files {
            let parent = file.deletingLastPathComponent().standardizedFileURL.resolvingSymlinksInPath()
            if parent.path.hasPrefix(prefix) { downloadedMediaRoots.insert(parent) }
        }
    }

    private func cleanupDownloadedMedia() {
        let manager = FileManager.default
        if let cache = mediaCacheRoot(), downloadedMediaRoots.isEmpty {
            try? manager.removeItem(at: cache)
        } else {
            for root in downloadedMediaRoots { try? manager.removeItem(at: root) }
        }
        downloadedMediaRoots.removeAll()
    }

    @objc private func chooseOutputFolder(_ sender: Any?) {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil else { return }
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url, let self else { return }
            self.defaults.set(url.path, forKey: "output.path")
            self.show(page: .processing)
        }
    }

    @objc private func chooseTranscript(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.plainText]
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url, let self else { return }
            do {
                let value = try String(contentsOf: url, encoding: .utf8)
                self.transcriptEditor?.string = value
                self.defaults.set(value, forKey: "llm.source")
            } catch {
                self.showNotice("Не удалось открыть транскрипт", error.localizedDescription)
            }
        }
    }

    @objc private func selectTemplate(_ sender: NSButton) {
        let prompt: String
        switch sender.identifier?.rawValue {
        case "Краткое содержание": prompt = "Сделай краткое содержание транскрипции и выдели основные решения."
        case "Извлечение задач": prompt = "Выдели задачи из транскрипции, ответственных и сроки, если они указаны."
        default:
            window.makeFirstResponder(promptEditor)
            return
        }
        promptEditor?.string = prompt
        defaults.set(prompt, forKey: "llm.prompt")
    }

    @objc private func openDocumentation(_ sender: NSButton) {
        let title = sender.identifier?.rawValue ?? "Открыть документацию"
        let detail: String
        switch title {
        case "Параметры":
            detail = "POST /api/v1/transcribe принимает multipart-поле file. Необязательные query-параметры: asr_backend, asr_model, enable_diarization, diarization_backend, num_speakers. Требуется заголовок X-API-Key."
        case "Форматы ответов":
            detail = "Успешная загрузка возвращает HTTP 202 и task_id. Это постановка в очередь, а не готовая транскрипция. Статус и результат запрашиваются отдельно."
        case "Эндпоинты":
            detail = "POST /api/v1/transcribe — загрузка одного файла.\nPOST /api/v1/transcribe/batch — пакетная загрузка.\nGET /health — состояние сервиса."
        case "Примеры":
            detail = "Выберите Python, cURL или JavaScript слева. Кнопка «Копировать пример» копирует показанный код. Задайте свой API-ключ и путь к аудиофайлу."
        default:
            detail = "Отдельный REST API запускается из api.py. Для запросов нужен X-API-Key. Примеры на этой странице соответствуют POST /api/v1/transcribe.\n\nЭтот нативный клиент не запускает сервер и не проверял его доступность."
        }
        showNotice(title, detail)
    }

    @objc private func openProject(_ sender: Any?) {
        guard let url = URL(string: "https://github.com/dubr1k/GigaAMGUI") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func openDeveloper(_ sender: NSButton) {
        guard let name = sender.identifier?.rawValue,
              ["Baggrisha", "dubr1k"].contains(name),
              let url = URL(string: "https://github.com/\(name)") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func copyAPIURL(_ sender: Any?) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("http://127.0.0.1:8000", forType: .string)
    }

    @objc private func copyCode(_ sender: Any?) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(apiCodeText?.string ?? "", forType: .string)
    }

    @objc private func apiLanguageChanged(_ sender: PillSelector) {
        let names = ["Python", "cURL", "JavaScript"]
        defaults.set(names[sender.selectedSegment], forKey: "api.exampleLanguage")
        apiCodeText?.string = apiExample(sender.selectedSegment)
    }

    func textDidChange(_ notification: Notification) {
        guard let editor = notification.object as? NSTextView, let key = editor.identifier?.rawValue else { return }
        defaults.set(editor.string, forKey: key)
    }

    private func refreshSelectedFiles() {
        refreshProcessingControls()
        let rows = selectedFileURLs.map { "\($0.lastPathComponent)  —  \(L10n.text(fileStates[$0.standardizedFileURL] ?? "выбран, не обработан"))" }
        selectedFilesLabel?.stringValue = rows.isEmpty ? L10n.text("Файлы не выбраны. Добавьте аудио или видео.") : rows.joined(separator: "\n")
        selectedFilesLabel?.toolTip = selectedFileURLs.map(\.path).joined(separator: "\n")
        selectedFilesLabel?.invalidateIntrinsicContentSize()
        if let document = selectedFilesLabel?.enclosingScrollView?.documentView {
            document.needsLayout = true
            document.layoutSubtreeIfNeeded()
        }
    }

    @objc private func resultTabChanged(_ sender: PillSelector) {
        guard resultPages.indices.contains(sender.selectedSegment) else { return }
        let page = resultPages[sender.selectedSegment]
        selectedResultTab = page.key
        resultTranscript?.string = page.text
        resultTranscript?.scrollToBeginningOfDocument(nil)
    }

    @objc private func resultFileChanged(_ sender: NSPopUpButton) {
        guard transcriptionResults.indices.contains(sender.indexOfSelectedItem) else { return }
        selectedResultURL = transcriptionResults[sender.indexOfSelectedItem].inputURL
        show(page: .result)
    }

    @objc private func resultOutputChanged(_ sender: NSPopUpButton) {
        selectedOutputFormat = sender.titleOfSelectedItem
    }

    @objc private func copyResult(_ sender: Any?) {
        guard let result = currentResult, !result.transcript.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(result.transcript, forType: .string)
    }

    @objc private func useResultForLLM(_ sender: Any?) {
        guard let result = currentResult, !result.transcript.isEmpty else { return }
        defaults.set(result.transcript, forKey: "llm.source")
        show(page: .llm)
    }

    @objc private func openResultOutput(_ sender: Any?) {
        guard let key = selectedOutputFormat, let url = existingOutputs[key] else {
            showNotice("Не удалось открыть файл", "Файл результата больше недоступен.")
            return
        }
        if !NSWorkspace.shared.open(url) { showNotice("Не удалось открыть файл", url.path) }
    }

    @objc private func revealResultOutputs(_ sender: Any?) {
        guard let key = selectedOutputFormat, let url = existingOutputs[key] else {
            showNotice("Не удалось открыть файл", "Файл результата больше недоступен.")
            return
        }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }



    @objc private func selectSettingsCategory(_ sender: NSButton) {
        guard let rawValue = sender.identifier?.rawValue,
              rawValue.hasPrefix("settings.category.") else { return }
        applySettingsCategorySelection(String(rawValue.dropFirst("settings.category.".count)))
        refreshProcessingControls()
    }


    @objc private func toggleLanguage(_ sender: Any?) {
        let current = defaults.string(forKey: "settings.language") ?? "Русский"
        defaults.set(current == "Русский" ? "English" : "Русский", forKey: "settings.language")
        rebuildInterface()
    }

    @objc private func toggleDarkTheme(_ sender: Any?) {
        defaults.set(Palette.isDark ? "Светлая" : "Тёмная", forKey: "settings.theme")
        rebuildInterface()
    }

    private func rebuildInterface() {
        window.makeFirstResponder(nil)
        let page = currentPage
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.buildWindowContent()
            self.show(page: page)
            self.window.makeKeyAndOrderFront(nil)
        }
    }

    @objc private func toggleSearch(_ sender: NSButton) {
        guard let searchField, let searchWidth else { return }
        let opening = searchField.isHidden
        searchField.isHidden = !opening
        if !opening {
            searchField.stringValue = ""
            searchResults.removeFromSuperview()
            window.makeFirstResponder(nil)
        }
        NSAnimationContext.runAnimationGroup { context in
            context.duration = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? 0 : 0.18
            context.allowsImplicitAnimation = true
            searchWidth.animator().constant = opening ? 340 : 44
            window.contentView?.layoutSubtreeIfNeeded()
        } completionHandler: { [weak self] in
            if opening { self?.window.makeFirstResponder(searchField) }
        }
    }

    func controlTextDidEndEditing(_ notification: Notification) {
        guard let field = notification.object as? NSTextField, field.identifier != nil else { return }
        textChanged(field)
    }

    func controlTextDidChange(_ notification: Notification) {
        guard let control = notification.object as? NSTextField else { return }
        if control.identifier != nil { textChanged(control) }
        guard let field = control as? NSSearchField, field === searchField else { return }
        searchResults.removeFromSuperview()
        let query = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return }
        let matches = Page.allCases.filter {
            L10n.text($0.title).localizedCaseInsensitiveContains(query)
                || L10n.text($0.navigationTitle).localizedCaseInsensitiveContains(query)
        }
        let rows = NSStackView()
        rows.orientation = .vertical
        rows.alignment = .leading
        rows.spacing = 4
        rows.edgeInsets = .init()
        for page in matches {
            let result = button(page.title, action: #selector(selectSearchResult(_:)))
            result.identifier = NSUserInterfaceItemIdentifier(page.rawValue)
            rows.addArrangedSubview(result)
            result.widthAnchor.constraint(equalTo: rows.widthAnchor).isActive = true
        }
        if matches.isEmpty { rows.addArrangedSubview(label("Ничего не найдено", size: 14, color: Palette.body)) }
        let surface = GlassView(radius: 28)
        embed(rows, in: surface.contentView, inset: 8, fillHeight: true)
        surface.translatesAutoresizingMaskIntoConstraints = false
        mainSurface.contentView.addSubview(surface, positioned: .above, relativeTo: nil)
        NSLayoutConstraint.activate([
            surface.leadingAnchor.constraint(equalTo: searchCapsule?.leadingAnchor ?? field.leadingAnchor),
            surface.topAnchor.constraint(equalTo: searchCapsule?.bottomAnchor ?? field.bottomAnchor, constant: 6),
            surface.widthAnchor.constraint(equalToConstant: 340)
        ])
        searchResults = surface
    }

    @objc private func selectSearchResult(_ sender: NSButton) {
        searchResults.removeFromSuperview()
        navigate(sender)
    }

    private func showNotice(_ title: String, _ message: String) {
        let alert = NSAlert()
        alert.messageText = L10n.text(title)
        alert.informativeText = L10n.text(message)
        alert.addButton(withTitle: L10n.text("Понятно"))
        alert.beginSheetModal(for: window)
    }
}

let application = NSApplication.shared
private let delegate = AppController()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
