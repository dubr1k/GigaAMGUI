import AppKit
import GigaAMLiquidCore
import UniformTypeIdentifiers

/// The LLM page: source text, templates, the LLM job, and the CLI provider
/// registry that the Python worker reports (badges and per-tool checks).
extension AppController {
    /// Mirrors `cli_tools.PROVIDERS` (order included). The Python registry is the
    /// source of truth; this copy only seeds the popup before the worker answers.
    static let llmProviders = ["API", "Claude Code", "Codex", "OpenCode", "Pi", "oh-my-pi", "Other"]

    /// Example flags shown as placeholders; each is a real option of that CLI.
    static let llmArgsExamples: [String: String] = [
        "claude": "--permission-mode bypassPermissions",
        "codex": "--dangerously-bypass-approvals-and-sandbox",
        "opencode": "--agent build",
        "pi": "--thinking low",
        "omp": "--thinking low --profile work",
    ]

    /// CLI providers with their settings-key prefix, default binary and whether the
    /// tool takes an inner `--provider` (pi / oh-my-pi).
    static let llmCliProviders: [(name: String, prefix: String, binary: String, hasProvider: Bool)] = [
        ("Claude Code", "claude", "claude", false), ("Codex", "codex", "codex", false),
        ("OpenCode", "opencode", "opencode", false), ("Pi", "pi", "pi", true), ("oh-my-pi", "omp", "omp", true),
    ]

    func buildLLM(into content: NSStackView) {
        let source = card("Исходный текст")
        let sourceBody = contentStack(source)
        sourceBody.spacing = 10
        sourceBody.addArrangedSubview(button("Выбрать файл с транскриптом", action: #selector(chooseTranscript(_:))))
        let providerPopup = popup(Self.llmProviders, key: "llm.provider")
        let providerStatus = label("", size: 12, color: Palette.muted)
        providerStatus.lineBreakMode = .byTruncatingMiddle
        providerStatus.setContentHuggingPriority(.required, for: .horizontal)
        providerStatus.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        providerStatus.setAccessibilityIdentifier("llm.providerStatus")
        llmProviderStatusLabel = providerStatus
        sourceBody.addArrangedSubview(equalColumns([
            compactField("Провайдер", control: horizontal([providerPopup, providerStatus], spacing: 10)),
            compactField("Модель", control: editableText(defaults.string(forKey: "llm.model") ?? "", key: "llm.model", placeholder: "gpt-4.1-mini"))
        ], spacing: 12))
        sourceBody.addArrangedSubview(wrappedLabel("Адрес, ключ и пути к CLI-провайдерам — в Настройки → LLM.", size: 12, color: Palette.muted))
        refreshLLMProviderStatus()
        refreshLLMTools(fresh: false)
        sourceBody.addArrangedSubview(label("Транскрипция · можно вставить текст", size: 12, color: Palette.body))
        let editor = textEditor(defaults.string(forKey: "llm.source") ?? "", key: "llm.source", height: nil, minHeight: 120)
        stretchy(editor)
        transcriptEditor = editor.documentView as? NSTextView
        transcriptEditor?.setAccessibilityLabel(L10n.text("Исходная транскрипция"))
        sourceBody.addArrangedSubview(editor)
        sourceBody.addArrangedSubview(wrappedLabel("Текст и параметры сохраняются на этом Mac.", size: 12, color: Palette.muted))
        sourceBody.bottomAnchor.constraint(equalTo: source.bottomAnchor, constant: -16).isActive = true

        let templates = card("Шаблоны")
        let templatesBody = contentStack(templates)
        templatesBody.spacing = 10
        templatesBody.addArrangedSubview(template("Краткое содержание", "Сжать текст в тезисы"))
        templatesBody.addArrangedSubview(template("Извлечение задач", "Выделить action items"))
        templatesBody.addArrangedSubview(template("Свой промпт", "Использовать инструкцию"))
        templatesBody.addArrangedSubview(wrappedLabel("Пользовательский промпт", size: 12, color: Palette.muted))
        let prompt = textEditor(defaults.string(forKey: "llm.prompt") ?? "", key: "llm.prompt", height: nil, minHeight: 90)
        stretchy(prompt)
        promptEditor = prompt.documentView as? NSTextView
        promptEditor?.setAccessibilityLabel(L10n.text("Пользовательский промпт"))
        templatesBody.addArrangedSubview(prompt)
        let run = button("Запустить обработку", primary: true, action: #selector(runLLM(_:)), height: 44)
        run.identifier = NSUserInterfaceItemIdentifier("llm.run")
        llmRunButton = run
        let cancel = button("Отменить запрос", action: #selector(cancelLLM(_:)), height: 44)
        cancel.identifier = NSUserInterfaceItemIdentifier("llm.cancel")
        llmCancelButton = cancel
        // Stacked: the side column is too narrow for both titles in one row.
        templatesBody.addArrangedSubview(vertical([run, cancel], spacing: 10))
        let status = wrappedLabel("", size: 12, color: Palette.muted)
        status.identifier = NSUserInterfaceItemIdentifier("llm.status")
        status.maximumNumberOfLines = 3
        llmStatusLabel = status
        templatesBody.addArrangedSubview(status)
        templates.widthAnchor.constraint(equalToConstant: 320).isActive = true
        templatesBody.bottomAnchor.constraint(equalTo: templates.bottomAnchor, constant: -16).isActive = true

        let output = card("Результат")
        let outputBody = contentStack(output)
        outputBody.spacing = 12
        let result = textEditor(llmResultText.isEmpty ? L10n.text("Ответа пока нет. Здесь появится результат запроса к выбранному провайдеру.") : llmResultText, key: nil, height: nil, minHeight: 120)
        stretchy(result)
        llmResultView = result.documentView as? NSTextView
        llmResultView?.isEditable = false
        llmResultView?.identifier = NSUserInterfaceItemIdentifier("llm.result")
        llmResultView?.setAccessibilityLabel(L10n.text("Результат"))
        outputBody.addArrangedSubview(result)
        let copy = button("Копировать", action: #selector(copyLLMResult(_:)), height: 34)
        copy.identifier = NSUserInterfaceItemIdentifier("llm.copy")
        llmCopyButton = copy
        let save = button("Сохранить .md", action: #selector(saveLLMResult(_:)), height: 34)
        save.identifier = NSUserInterfaceItemIdentifier("llm.save")
        llmSaveButton = save
        outputBody.addArrangedSubview(equalColumns([copy, save], spacing: 12))
        outputBody.bottomAnchor.constraint(equalTo: output.bottomAnchor, constant: -16).isActive = true
        content.addArrangedSubview(stretchy(fillRow([vertical([stretchy(source), stretchy(output)], spacing: 16), templates], spacing: 16)))
        // Both editors of the left column grow; equal heights split the extra room.
        result.heightAnchor.constraint(equalTo: editor.heightAnchor).isActive = true
        refreshLLMControls()
    }

    func template(_ title: String, _ detail: String) -> NSButton {
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

    @objc func chooseTranscript(_ sender: Any?) {
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

    @objc func selectTemplate(_ sender: NSButton) {
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

    /// Same shape as the PyQt client's `_collect_llm_settings`, so `llm_service` needs no adapter.
    func llmSettings() throws -> [String: Any] {
        let provider = option("llm.provider", values: Self.llmProviders)
        func text(_ key: String, _ fallback: String = "") -> String {
            let value = (defaults.string(forKey: key) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return value.isEmpty ? fallback : value
        }
        let apiURL = text("llm.apiUrl")
        let apiKey = (SecureStore.string(for: "llmApiKey") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let model = text("llm.model")
        guard let temperature = Double(text("llm.temperature", "0.2")), (0...2).contains(temperature) else {
            throw WorkerFailure(L10n.text("Temperature должно быть числом в диапазоне 0..2"))
        }
        if provider == "API" {
            guard !apiURL.isEmpty else { throw WorkerFailure(L10n.text("Укажите API URL в Настройки → LLM")) }
            guard !apiKey.isEmpty else { throw WorkerFailure(L10n.text("Укажите API Key в Настройки → LLM")) }
            guard !model.isEmpty else { throw WorkerFailure(L10n.text("Укажите модель")) }
        }
        if provider == "Other", text("llm.otherPath").isEmpty {
            throw WorkerFailure(L10n.text("Укажите команду для провайдера «Другое» в Настройки → LLM"))
        }
        return [
            "provider": provider, "api_url": apiURL, "api_key": apiKey, "model": model, "temperature": temperature,
            "claude_path": text("llm.claudePath", "claude"), "claude_args": text("llm.claudeArgs"),
            "codex_path": text("llm.codexPath", "codex"), "codex_args": text("llm.codexArgs"),
            "opencode_path": text("llm.opencodePath", "opencode"), "opencode_args": text("llm.opencodeArgs"),
            "pi_path": text("llm.piPath", "pi"), "pi_provider": text("llm.piProvider"), "pi_args": text("llm.piArgs"),
            "omp_path": text("llm.ompPath", "omp"), "omp_provider": text("llm.ompProvider"), "omp_args": text("llm.ompArgs"),
            "other_path": text("llm.otherPath"), "other_args": text("llm.otherArgs"),
            "llm_allow_tools": enabledOption("llm.allowTools", defaultValue: false)
        ]
    }

    // MARK: - CLI tools (the registry lives in the Python worker)

    static let llmToolsCacheKey = "llm.toolsCache"

    func loadLLMToolsCache() {
        guard let data = defaults.data(forKey: Self.llmToolsCacheKey),
              let objects = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return }
        for status in objects.compactMap(LLMToolStatus.init) { llmToolStatuses[status.provider] = status }
    }

    func saveLLMToolsCache() {
        let objects = llmToolStatuses.values.map(\.dictionary)
        if let data = try? JSONSerialization.data(withJSONObject: objects) { defaults.set(data, forKey: Self.llmToolsCacheKey) }
    }

    /// User-entered paths, keyed by registry id, for the worker's `overrides`.
    func llmToolOverrides() -> [String: String] {
        var overrides: [String: String] = [:]
        for tool in Self.llmCliProviders {
            let value = (defaults.string(forKey: "llm.\(tool.prefix)Path") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if !value.isEmpty && value != tool.binary { overrides[tool.prefix] = value }
        }
        return overrides
    }

    func refreshLLMTools(fresh: Bool) {
        guard llmToolsQuery == nil, !isClosing else { return }
        llmRescanButton?.isEnabled = false
        let query = LLMToolsQuery.scan(overrides: llmToolOverrides(), fresh: fresh) { [weak self] result in
            guard let self else { return }
            self.llmToolsQuery = nil
            self.llmRescanButton?.isEnabled = true
            switch result {
            case .tools(_, let tools):
                for tool in tools { self.llmToolStatuses[tool.provider] = tool }
                self.saveLLMToolsCache()
            case .tool(let tool):
                self.llmToolStatuses[tool.provider] = tool
            case .failed(let message):
                self.transcriptionLog += "LLM tools: \(message)\n"
            }
            self.refreshLLMToolRows()
            self.refreshLLMProviderStatus()
        }
        query.onLog = { [weak self] line in self?.appendProcessingLog("LLM tools: " + line) }
        llmToolsQuery = query
        query.start()
    }

    @objc func rescanLLMTools(_ sender: Any?) {
        refreshLLMTools(fresh: true)
    }

    func checkLLMTool(_ provider: String) {
        guard llmToolChecks[provider] == nil, !isClosing,
              let tool = Self.llmCliProviders.first(where: { $0.name == provider }) else { return }
        let path = (defaults.string(forKey: "llm.\(tool.prefix)Path") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        llmToolRows[provider]?.check.isEnabled = false
        let query = LLMToolsQuery.check(provider: provider, path: path) { [weak self] result in
            guard let self else { return }
            self.llmToolChecks[provider] = nil
            self.llmToolRows[provider]?.check.isEnabled = true
            if case .tool(let status) = result {
                self.llmToolStatuses[status.provider] = status
                self.saveLLMToolsCache()
            } else if case .failed(let message) = result {
                self.transcriptionLog += "LLM tools: \(message)\n"
            }
            self.refreshLLMToolRows()
            self.refreshLLMProviderStatus()
        }
        query.onLog = { [weak self] line in self?.appendProcessingLog("LLM tools: " + line) }
        llmToolChecks[provider] = query
        query.start()
    }

    @objc func checkLLMToolButton(_ sender: NSButton) {
        guard let provider = sender.identifier?.rawValue.replacingOccurrences(of: "llm.check.", with: "") else { return }
        checkLLMTool(provider)
    }

    @objc func browseLLMTool(_ sender: NSButton) {
        guard let provider = sender.identifier?.rawValue.replacingOccurrences(of: "llm.browse.", with: ""),
              let tool = Self.llmCliProviders.first(where: { $0.name == provider }) else { return }
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.showsHiddenFiles = true
        panel.message = L10n.text("Выберите исполняемый файл") + " \(tool.name)"
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self, response == .OK, let url = panel.url else { return }
            self.defaults.set(url.path, forKey: "llm.\(tool.prefix)Path")
            self.llmToolRows[provider]?.path.stringValue = url.path
            self.checkLLMTool(provider)
        }
    }

    static let llmStatusGlyph: [String: String] = ["found": "●", "missing": "○", "broken": "⚠"]

    func llmStatusColor(_ status: String) -> NSColor {
        switch status {
        case "found": return NSColor.systemGreen
        case "broken": return NSColor.systemOrange
        default: return Palette.muted
        }
    }

    func llmStatusText(_ status: LLMToolStatus?) -> String {
        guard let status else { return L10n.text("проверка…") }
        switch status.status {
        case "found": return status.version ?? L10n.text("найден")
        case "broken": return L10n.text("не запускается")
        default: return L10n.text("не найден")
        }
    }

    func llmToolRow(_ tool: (name: String, prefix: String, binary: String, hasProvider: Bool)) -> NSView {
        let dot = label("○", size: 14, weight: .bold, color: Palette.muted)
        dot.widthAnchor.constraint(equalToConstant: 16).isActive = true
        let name = label(tool.name, size: 14, weight: .medium, color: Palette.ink)
        name.widthAnchor.constraint(equalToConstant: 100).isActive = true
        let version = label("", size: 12, color: Palette.muted)
        version.widthAnchor.constraint(equalToConstant: 64).isActive = true
        version.lineBreakMode = .byTruncatingTail
        let path = editableText(defaults.string(forKey: "llm.\(tool.prefix)Path") ?? "", key: "llm.\(tool.prefix)Path", placeholder: tool.binary)
        path.font = NSFont.systemFont(ofSize: 12)
        path.lineBreakMode = .byTruncatingMiddle
        path.heightAnchor.constraint(equalToConstant: 30).isActive = true
        path.setContentHuggingPriority(.defaultLow, for: .horizontal)
        path.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let browse = button("Обзор…", action: #selector(browseLLMTool(_:)), height: 30)
        browse.identifier = NSUserInterfaceItemIdentifier("llm.browse.\(tool.name)")
        browse.setContentCompressionResistancePriority(.required, for: .horizontal)
        let check = button("Проверить", action: #selector(checkLLMToolButton(_:)), height: 30)
        check.identifier = NSUserInterfaceItemIdentifier("llm.check.\(tool.name)")
        check.setContentCompressionResistancePriority(.required, for: .horizontal)
        llmToolRows[tool.name] = (dot: dot, version: version, path: path, check: check)
        // The path gets a line of its own: beside five fixed-width controls it was
        // squeezed to a sliver once the settings panel follows the window width.
        let header = horizontal([dot, name, version, flexibleSpace(), browse, check], spacing: 8)
        header.alignment = .centerY
        let row = vertical([header, path], spacing: 6)
        row.setAccessibilityIdentifier("llm.tool.\(tool.name)")
        return row
    }

    func refreshLLMToolRows() {
        for (provider, row) in llmToolRows {
            let status = llmToolStatuses[provider]
            row.dot.stringValue = Self.llmStatusGlyph[status?.status ?? ""] ?? "○"
            row.dot.textColor = llmStatusColor(status?.status ?? "")
            row.version.stringValue = llmStatusText(status)
            let hint: String
            if let status, status.status == "found", let path = status.path {
                hint = path
                if row.path.stringValue.isEmpty { row.path.placeholderString = path }
            } else if let status, status.status == "broken" {
                hint = status.detail ?? ""
            } else if let status, !status.installHint.isEmpty {
                hint = L10n.text("Установка") + ": " + status.installHint
            } else {
                hint = ""
            }
            row.dot.toolTip = hint
            row.version.toolTip = hint
        }
    }

    func refreshLLMProviderStatus() {
        guard let label = llmProviderStatusLabel else { return }
        let provider = option("llm.provider", values: Self.llmProviders)
        switch provider {
        case "API":
            label.stringValue = "HTTP API"
            label.textColor = Palette.muted
            label.toolTip = nil
        case "Other":
            label.stringValue = L10n.text("произвольная команда")
            label.textColor = Palette.muted
            label.toolTip = nil
        default:
            let status = llmToolStatuses[provider]
            let glyph = Self.llmStatusGlyph[status?.status ?? ""] ?? "○"
            label.stringValue = "\(glyph) \(llmStatusText(status))"
            label.textColor = status == nil ? Palette.muted : llmStatusColor(status!.status)
            label.toolTip = status?.path ?? status?.detail ?? (status.map { L10n.text("Установка") + ": " + $0.installHint })
        }
    }

    @objc func runLLM(_ sender: Any?) {
        window.makeFirstResponder(nil)
        guard llmJob == nil, !isClosing else { return }
        let source = (transcriptEditor?.string ?? defaults.string(forKey: "llm.source") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !source.isEmpty else {
            showNotice("Не удалось запустить LLM", "Выберите файл с транскриптом или вставьте текст.")
            return
        }
        let prompt = (promptEditor?.string ?? defaults.string(forKey: "llm.prompt") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty else {
            showNotice("Не удалось запустить LLM", "Выберите шаблон или введите пользовательский промпт.")
            return
        }
        let settings: [String: Any]
        do { settings = try llmSettings() } catch { showNotice("LLM не настроена", error.localizedDescription); return }
        llmResultText = ""
        llmResultView?.string = ""
        llmStatusLabel?.stringValue = L10n.text("Запрос отправлен…")
        let request = LLMRequest(text: source, modes: ["custom"], prompt: prompt, settings: settings, outputDirectory: outputDirectory)
        let job = LLMJob(request: request) { [weak self] event in self?.receiveLLMEvent(event) }
        llmJob = job
        refreshLLMControls()
        job.start()
    }

    @objc func cancelLLM(_ sender: Any?) { llmJob?.cancel() }

    func receiveLLMEvent(_ event: LLMJobEvent) {
        switch event {
        case .started:
            llmStatusLabel?.stringValue = L10n.text("Ожидание ответа провайдера…")
        case .chunk(_, let text):
            llmResultText += text
            llmResultView?.string = llmResultText
            scrollToTail(llmResultView)
        case .completed(let results, let saved):
            llmResultText = results.map(\.text).joined(separator: "\n\n")
            llmResultView?.string = llmResultText
            let names = saved.map(\.lastPathComponent).joined(separator: ", ")
            llmStatusLabel?.stringValue = names.isEmpty ? L10n.text("Готово.") : L10n.text("Готово. Сохранено: ") + names
            finishLLM()
        case .cancelled:
            llmStatusLabel?.stringValue = L10n.text("Запрос отменён.")
            finishLLM()
        case .failed(let message):
            llmStatusLabel?.stringValue = message
            transcriptionLog += message + "\n"
            finishLLM()
        case .log(let message):
            appendProcessingLog(message)
        }
    }

    func finishLLM() {
        llmJob = nil
        if isTerminating { replyWhenJobsFinished(); return }
        refreshLLMControls()
    }

    func refreshLLMControls() {
        let running = llmJob != nil
        llmRunButton?.isEnabled = !running && !isClosing
        llmCancelButton?.isEnabled = running
        llmCopyButton?.isEnabled = !llmResultText.isEmpty
        llmSaveButton?.isEnabled = !llmResultText.isEmpty
    }

    @objc func copyLLMResult(_ sender: Any?) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(llmResultText, forType: .string)
    }

    @objc func saveLLMResult(_ sender: Any?) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "llm_result.md"
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url, let self else { return }
            do { try self.llmResultText.write(to: url, atomically: true, encoding: .utf8) }
            catch { self.showNotice("Не удалось сохранить", error.localizedDescription) }
        }
    }
}
