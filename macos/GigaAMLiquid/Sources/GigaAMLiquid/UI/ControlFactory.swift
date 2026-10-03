import AppKit

/// Builders for the Liquid controls every page uses: cards, stacks, fields,
/// popups, switches and buttons, each bound to its UserDefaults key through
/// its identifier, plus the layout helpers of the responsive pages.
extension AppController {
    /// A transparent, vertically scrolling wrapper for a settings body that may be
    /// taller than its panel. Outer page scrolling takes over at either end.
    func scrollable(_ body: NSStackView, inset: CGFloat = 6) -> NSScrollView {
        // Auto Layout document: its height follows the stack, so the scroll view
        // knows the real content height without a manual layout pass.
        let document = AutoLayoutDocumentView()
        document.translatesAutoresizingMaskIntoConstraints = false
        body.translatesAutoresizingMaskIntoConstraints = false
        document.addSubview(body)
        let scroll = EditorScrollView()
        scroll.drawsBackground = false
        scroll.documentView = document
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.contentView.drawsBackground = false
        NSLayoutConstraint.activate([
            document.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            document.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            document.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
            // The focus ring is drawn ~4 pt outside a field; fields flush with the
            // document edge would have it clipped by the scroll view's clip view.
            body.leadingAnchor.constraint(equalTo: document.leadingAnchor, constant: inset),
            body.trailingAnchor.constraint(equalTo: document.trailingAnchor, constant: -inset),
            body.topAnchor.constraint(equalTo: document.topAnchor, constant: inset),
            body.bottomAnchor.constraint(equalTo: document.bottomAnchor, constant: -inset)
        ])
        return scroll
    }

    func size(_ view: NSView, width: CGFloat, height: CGFloat) {
        view.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            view.widthAnchor.constraint(equalToConstant: width),
            view.heightAnchor.constraint(equalToConstant: height)
        ])
    }

    func vertical(_ views: [NSView], spacing: CGFloat, alignment: NSLayoutConstraint.Attribute = .width) -> NSStackView {
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

    /// Marks the part of a stack that takes the height left over. A stack gives it
    /// to the arranged view with the lowest vertical hugging; cards and labels all
    /// sit at 250, so without this mark the extra room went to whichever won the tie
    /// (a status label under the buttons, the gap under the Live controls). Mark
    /// every level on the way down: the row in the page, the card in its column,
    /// the editor or list in its card.
    @discardableResult
    func stretchy<View: NSView>(_ view: View) -> View {
        view.setContentHuggingPriority(NSLayoutConstraint.Priority(1), for: .vertical)
        // A card's glass effect view hosts SwiftUI content that hugs its own height
        // at 250; relax it too, or the card resists growing as much as a label does.
        if let glass = view as? GlassView {
            var pending = glass.subviews.filter { $0 !== glass.contentView }
            while let next = pending.popLast() {
                next.setContentHuggingPriority(NSLayoutConstraint.Priority(1), for: .vertical)
                pending += next.subviews.filter { $0 !== glass.contentView }
            }
        }
        return view
    }

    /// Columns that all take the row's full height: with one column (or a card in
    /// it) left without a fixed height, that part stretches with the window.
    func fillRow(_ views: [NSView], spacing: CGFloat) -> NSStackView {
        let row = horizontal(views, spacing: spacing)
        for view in views { view.heightAnchor.constraint(equalTo: row.heightAnchor).isActive = true }
        return row
    }

    func equalColumns(_ views: [NSView], spacing: CGFloat) -> NSStackView {
        let stack = horizontal(views, spacing: spacing)
        stack.distribution = .fillEqually
        return stack
    }

    func flexibleSpace() -> NSView {
        let view = NSView()
        view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return view
    }

    func centered(_ view: NSView) -> NSView {
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

    func embed(_ child: NSView, in parent: NSView, inset: CGFloat, top: CGFloat? = nil, fillHeight: Bool = false) {
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

    func insetPanel() -> NSView {
        let view = NSView()
        view.wantsLayer = true
        view.layer?.cornerRadius = 12
        view.layer?.borderWidth = 0.5
        view.layer?.borderColor = Palette.line.withAlphaComponent(0.75).cgColor
        view.layer?.backgroundColor = NSColor.white.withAlphaComponent(Palette.isDark ? 0.025 : 0.24).cgColor
        return view
    }

    func wrappedLabel(_ text: String, size: CGFloat, color: NSColor) -> NSTextField {
        let field = label(text, size: size, color: color)
        field.maximumNumberOfLines = 0
        field.lineBreakMode = .byWordWrapping
        return field
    }

    /// A `nil` width marks the column that takes the remaining width; rows built
    /// with the same widths line up under the headings.
    func columnHeadings(_ columns: [(String, CGFloat?)], trailing: CGFloat = 0) -> NSView {
        var views = columns.map { title, width -> NSView in
            let text = label(title, size: 12, color: Palette.muted)
            if let width {
                text.widthAnchor.constraint(equalToConstant: width).isActive = true
            } else {
                text.setContentHuggingPriority(.defaultLow - 1, for: .horizontal)
            }
            return text
        }
        if trailing > 0 {
            let slot = NSView()
            slot.widthAnchor.constraint(equalToConstant: trailing).isActive = true
            views.append(slot)
        }
        if !columns.contains(where: { $0.1 == nil }) { views.append(flexibleSpace()) }
        return horizontal(views, spacing: 8)
    }

    func symbol(_ name: String, size: CGFloat) -> NSImageView {
        let image = NSImageView()
        image.image = NSImage(systemSymbolName: name, accessibilityDescription: nil)
        image.contentTintColor = Palette.blue
        self.size(image, width: size, height: size)
        return image
    }

    /// `minHeight` instead of `height` makes the editor the stretchy part of its card.
    func textEditor(_ value: String, key: String?, height: CGFloat?, minHeight: CGFloat? = nil) -> NSScrollView {
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
        if let minHeight { scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: minHeight).isActive = true }
        return scroll
    }

    func card(_ title: String, trailing: NSView? = nil, dense: Bool = false) -> GlassView {
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

    func compactCard(_ title: String) -> GlassView {
        card(title, dense: true)
    }

    func contentStack(_ card: GlassView) -> NSStackView {
        guard let stack = card.contentView.subviews.compactMap({ $0 as? NSStackView }).first(where: { $0.identifier?.rawValue == "card.body" }) else { fatalError("Card body missing") }
        return stack
    }

    func compactField(_ title: String, control: NSView) -> NSView {
        let stack = vertical([label(title, size: 11, color: Palette.muted), control], spacing: 4)
        control.heightAnchor.constraint(equalToConstant: 28).isActive = true
        control.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        control.setAccessibilityLabel(L10n.text(title))
        return stack
    }

    func settingsField(_ title: String, control: NSView) -> NSView {
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

    func inactive(_ view: NSView) -> NSView {
        if let control = view as? NSControl { control.isEnabled = false }
        view.subviews.forEach { _ = inactive($0) }
        view.toolTip = L10n.text("Параметр задаёт рабочий сервис; отдельное управление недоступно.")
        return view
    }

    func popup(_ values: [String], key: String) -> NSPopUpButton {
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

    /// Secure text field whose value lives in the Keychain (`SecureStore`) under `account`; `key` is only the control identifier.
    func secureField(account: String, key: String) -> NSTextField {
        let value = SecureStore.string(for: account) ?? ""
        let field = RoundedSecureTextField(string: value)
        field.cell = CenteredSecureTextCell(textCell: value)
        field.isEditable = true
        field.isSelectable = true
        field.isBezeled = false
        field.drawsBackground = false
        field.wantsLayer = true
        field.cornerRadius = 18
        field.layer?.masksToBounds = true
        field.placeholderString = L10n.text("Не настроен")
        field.identifier = NSUserInterfaceItemIdentifier(key)
        field.target = self
        field.delegate = self
        field.action = #selector(textChanged(_:))
        return field
    }

    func editableText(_ value: String, key: String, placeholder: String) -> NSTextField {
        let field = RoundedTextField(string: value)
        field.cell = CenteredTextCell(textCell: value)
        field.isEditable = true
        field.isSelectable = true
        field.drawsBackground = false
        field.isBezeled = false
        field.wantsLayer = true
        field.cornerRadius = 16
        field.layer?.masksToBounds = true
        field.placeholderString = L10n.text(placeholder)
        field.font = NSFont.systemFont(ofSize: 14)
        field.identifier = NSUserInterfaceItemIdentifier(key)
        field.target = self
        field.delegate = self
        field.action = #selector(textChanged(_:))
        return field
    }

    func toggleRow(_ title: String, key: String, defaultValue: Bool) -> NSView {
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

    func checkbox(_ title: String, key: String, defaultValue: Bool) -> NSButton {
        let box = RoundedCheckButton(checkboxWithTitle: L10n.text(title), target: self, action: #selector(switchChanged(_:)))

        box.identifier = NSUserInterfaceItemIdentifier(key)
        box.state = defaults.object(forKey: key) == nil ? (defaultValue ? .on : .off) : (defaults.bool(forKey: key) ? .on : .off)
        box.font = NSFont.systemFont(ofSize: 12)
        box.controlSize = .small
        box.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        box.heightAnchor.constraint(equalToConstant: 18).isActive = true
        return box
    }

    func codeView(_ code: String) -> NSScrollView {
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

    func label(_ text: String, size: CGFloat, weight: NSFont.Weight = .regular, color: NSColor) -> NSTextField {
        let label = NSTextField(wrappingLabelWithString: L10n.text(text))
        label.font = NSFont.systemFont(ofSize: size, weight: weight)
        label.textColor = color
        label.maximumNumberOfLines = 1
        label.lineBreakMode = .byTruncatingTail
        label.setContentCompressionResistancePriority(.required, for: .vertical)
        label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return label
    }

    func horizontal(_ views: [NSView], spacing: CGFloat) -> NSStackView {
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

    func divider() -> NSBox {
        let box = NSBox()
        box.boxType = .separator
        return box
    }

    func button(_ title: String, primary: Bool = false, action: Selector? = nil, height: CGFloat = 38) -> NSButton {
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

    func pill(_ title: String, width: CGFloat, action: Selector?) -> NSButton {
        let button = button(title, action: action)
        button.widthAnchor.constraint(equalToConstant: width).isActive = true
        return button
    }

    func iconButton(_ name: String, hint: String, action: Selector? = nil) -> NSButton {
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

    /// `scrollToEndOfDocument` also scrolls horizontally to the end of a long line;
    /// revealing the last character keeps the wrapped text pinned to the left edge.
    func scrollToTail(_ view: NSTextView?) {
        guard let view else { return }
        view.scrollRangeToVisible(NSRange(location: (view.string as NSString).length, length: 0))
        if let clip = view.enclosingScrollView?.contentView, clip.bounds.origin.x != 0 {
            clip.scroll(to: NSPoint(x: 0, y: clip.bounds.origin.y))
            view.enclosingScrollView?.reflectScrolledClipView(clip)
        }
    }
}
