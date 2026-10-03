import AppKit

/// The Результат page: the selected file's transcript and saved outputs.
extension AppController {
    func buildResult(into content: NSStackView) {
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
        let editor = textEditor(resultPages.isEmpty ? L10n.text("Транскрипция появится после обработки файла.") : resultPages[selectedTab].text, key: nil, height: nil, minHeight: 200)
        stretchy(editor)
        resultTranscript = editor.documentView as? NSTextView
        resultTranscript?.isEditable = false
        resultTranscript?.identifier = NSUserInterfaceItemIdentifier("result.content")
        resultTranscript?.setAccessibilityLabel(L10n.text("Результат обработки"))
        body.addArrangedSubview(editor)
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
        content.addArrangedSubview(stretchy(fillRow([result, useful], spacing: 16)))
    }

    private var currentResult: NativeTranscriptionResult? {
        transcriptionResults.first { $0.inputURL == selectedResultURL } ?? transcriptionResults.first
    }

    private var existingOutputs: [String: URL] {
        (currentResult?.outputFiles ?? [:]).filter { FileManager.default.isReadableFile(atPath: $0.value.path) }
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
}
