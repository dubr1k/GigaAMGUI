import AppKit

/// The Журнал page (an empty history until the worker keeps one).
extension AppController {
    func buildHistory(into content: NSStackView) {
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
        let columns: [(String, CGFloat?)] = [("Файл", nil), ("Длительность", 140), ("Статус", 134), ("Дата", 180)]
        body.addArrangedSubview(columnHeadings(columns))
        body.addArrangedSubview(divider())
        let table = NSTableView()
        table.headerView = nil
        table.backgroundColor = .clear
        table.rowHeight = 56
        table.intercellSpacing = NSSize(width: 12, height: 0)
        table.gridStyleMask = .solidHorizontalGridLineMask
        table.gridColor = Palette.line.withAlphaComponent(0.65)
        table.columnAutoresizingStyle = .firstColumnOnlyAutoresizingStyle
        for (title, width) in columns {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(title))
            column.title = L10n.text(title)
            column.width = width ?? 240
            if width != nil { column.resizingMask = [] }
            table.addTableColumn(column)
        }
        table.setAccessibilityLabel(L10n.text("История обработок: записей нет"))
        let tableScroll = NSScrollView()
        tableScroll.drawsBackground = false
        tableScroll.documentView = table
        tableScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 160).isActive = true
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
        body.addArrangedSubview(stretchy(tableArea))
        body.addArrangedSubview(label("0 записей", size: 12, color: Palette.muted))
        body.bottomAnchor.constraint(equalTo: history.bottomAnchor, constant: -16).isActive = true
        content.addArrangedSubview(stretchy(history))
    }
}
