import AppKit

final class PillSelector: NSControl {
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
