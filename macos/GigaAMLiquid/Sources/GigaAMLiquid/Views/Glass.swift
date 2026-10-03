import AppKit

final class ApplicationWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

final class BlobBackgroundView: NSVisualEffectView {
    /// Files dropped anywhere on the window; returns true when at least one was accepted.
    var dropHandler: (([URL]) -> Bool)?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        material = .underWindowBackground
        blendingMode = .behindWindow
        state = .active
        wantsLayer = true
        layer?.backgroundColor = NSColor(calibratedWhite: Palette.isDark ? 0.08 : 0.97, alpha: 0.18).cgColor
        registerForDraggedTypes([.fileURL])
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    private func fileURLs(from sender: NSDraggingInfo) -> [URL] {
        let options: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        return sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: options) as? [URL] ?? []
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        dropHandler != nil && !fileURLs(from: sender).isEmpty ? .copy : []
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        draggingEntered(sender)
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        guard let dropHandler else { return false }
        return dropHandler(fileURLs(from: sender))
    }
}

final class GlassView: NSView {
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
