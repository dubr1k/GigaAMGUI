import AppKit
import GigaAMLiquidCore
import UniformTypeIdentifiers

/// The Обработка page: file selection, batch settings, the output folder, the
/// batch transcription job and its progress and log.
extension AppController {
    func buildProcessing(into content: NSStackView) {
        let upload = card("Загрузка файлов")
        let zone = DropZoneView()
        let zoneText = vertical([
            symbol("square.and.arrow.up", size: 28),
            label("Перетащите сюда аудио, видео или папку", size: 17, weight: .regular, color: Palette.ink),
            label(".wav, .mp3, .m4a, .mp4, .mov, .mkv · папка сканируется целиком", size: 13, color: Palette.body)
        ], spacing: 6, alignment: .centerX)
        zoneText.translatesAutoresizingMaskIntoConstraints = false
        zone.addSubview(zoneText)
        NSLayoutConstraint.activate([
            zoneText.centerXAnchor.constraint(equalTo: zone.centerXAnchor),
            zoneText.centerYAnchor.constraint(equalTo: zone.centerYAnchor),
            zone.heightAnchor.constraint(equalToConstant: 88)
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
        uploadStack.bottomAnchor.constraint(equalTo: upload.bottomAnchor, constant: -16).isActive = true

        let processing = card("Настройки обработки", dense: true)
        let settings = contentStack(processing)
        settings.spacing = 12
        settings.addArrangedSubview(compactField("Подготовка аудио", control: popup(SettingsSchema.audioPreprocessing, key: "processing.preprocessing")))
        settings.addArrangedSubview(compactField("Модель", control: popup(SettingsSchema.models, key: "settings.model")))
        settings.addArrangedSubview(toggleRow("Диаризация", key: "settings.diarization", defaultValue: false))
        settings.addArrangedSubview(compactField("Кол-во спикеров", control: speakerCountPopup()))
        settings.bottomAnchor.constraint(equalTo: processing.bottomAnchor, constant: -16).isActive = true

        let clear = button("Очистить", action: #selector(clearFiles(_:)), height: 30)
        clear.identifier = NSUserInterfaceItemIdentifier("processing.clear")
        // Счётчик в шапке карточки: «2 файла» рядом с «Очистить», чтобы размер очереди был
        // виден без прокрутки списка.
        let count = label("", size: 13, color: Palette.muted)
        count.identifier = NSUserInterfaceItemIdentifier("processing.selected.count")
        selectedFilesCountLabel = count
        let trailing = horizontal([count, clear], spacing: 12)
        trailing.alignment = .centerY
        let selected = card("Выбранные файлы", trailing: trailing)
        let selectedStack = contentStack(selected)
        selectedStack.addArrangedSubview(columnHeadings(selectedFileColumns, trailing: Self.selectedFileRemoveWidth))
        selectedStack.addArrangedSubview(divider())
        let filenames = wrappedLabel("Файлы не выбраны. Добавьте аудио или видео.", size: 14, color: Palette.body)
        selectedFilesLabel = filenames
        // Пустая подпись и строки живут в одном стеке и подменяют друг друга: vertical()
        // не отсоединяет скрытые view, и спрятанная подпись оставляла бы зазор над списком.
        let rows = vertical([], spacing: 6)
        rows.identifier = NSUserInterfaceItemIdentifier("processing.selected.rows")
        selectedFilesRows = rows
        // The list is the stretchy part of the page: it takes the height the window
        // leaves and scrolls on its own once the files no longer fit.
        let rowsScroll = stretchy(scrollable(rows, inset: 0))
        rowsScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 40).isActive = true
        selectedStack.addArrangedSubview(rowsScroll)
        refreshSelectedFiles()
        selectedStack.bottomAnchor.constraint(equalTo: selected.bottomAnchor, constant: -16).isActive = true

        let folder = compactCard("Папка сохранения результатов")
        let path = editableText(outputPathText, key: "output.path", placeholder: "Рядом с исходным файлом")
        path.heightAnchor.constraint(equalToConstant: 36).isActive = true
        path.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let change = button("Изменить", action: #selector(chooseOutputFolder(_:)), height: 36)
        change.identifier = NSUserInterfaceItemIdentifier("output.choose")
        change.widthAnchor.constraint(equalToConstant: 92).isActive = true
        contentStack(folder).addArrangedSubview(horizontal([path, change], spacing: 12))
        let folderHint = wrappedLabel("Пустое поле — результаты сохраняются в папку исходного файла.", size: 12, color: Palette.muted)
        contentStack(folder).addArrangedSubview(folderHint)
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
        formats.addArrangedSubview(checkbox("Диаризация (.txt)", key: "output.diarize", defaultValue: false))
        formats.addArrangedSubview(checkbox("Диар. + таймкоды", key: "output.diarizeTimestamps", defaultValue: false))
        formats.addArrangedSubview(divider())
        formats.addArrangedSubview(label("Настройки субтитров", size: 15, weight: .medium, color: Palette.ink))
        formats.addArrangedSubview(equalColumns([
            compactField("Строк в блоке", control: popup(SettingsSchema.subtitleLines, key: "subtitle.lines")),
            compactField("Символов", control: popup(SettingsSchema.subtitleCharacters, key: "subtitle.characters"))
        ], spacing: 8))
        formats.addArrangedSubview(toggleRow("Разбивать по предложениям", key: "subtitle.sentences", defaultValue: true))
        // No bottom pin: the card stretches to end level with the left column.
        let start = button("Запустить обработку", primary: true, action: #selector(startProcessing(_:)), height: 44)
        start.identifier = NSUserInterfaceItemIdentifier("transcription.start")
        start.setContentHuggingPriority(.defaultLow, for: .horizontal)
        startProcessingButton = start
        let cancel = button("Остановить после текущего файла", action: #selector(cancelProcessing(_:)), height: 44)
        cancel.identifier = NSUserInterfaceItemIdentifier("transcription.cancel")
        cancel.setContentHuggingPriority(.required, for: .horizontal)
        cancel.setContentCompressionResistancePriority(.required, for: .horizontal)
        cancelProcessingButton = cancel
        let validation = wrappedLabel("", size: 12, color: Palette.muted)
        validation.identifier = NSUserInterfaceItemIdentifier("transcription.validation")
        processingValidationLabel = validation
        // Files and actions on the left, the settings column on the right: the page
        // fits a default window, and in a larger one the file list gets the room.
        let files = vertical([upload, stretchy(selected), folder, horizontal([start, cancel], spacing: 12), validation], spacing: 16)
        files.setCustomSpacing(8, after: files.arrangedSubviews[3])
        let options = vertical([processing, stretchy(output)], spacing: 16)
        options.widthAnchor.constraint(equalToConstant: 252).isActive = true
        content.addArrangedSubview(stretchy(fillRow([files, options], spacing: 16)))
        content.addArrangedSubview(progressCard())
    }

    func progressCard() -> GlassView {
        let view = GlassView(radius: 16)
        let progress = ProgressTrackView()
        progress.heightAnchor.constraint(equalToConstant: 8).isActive = true
        progress.setContentHuggingPriority(.defaultLow, for: .horizontal)
        progressTrack = progress
        let percentage = label("", size: 12, color: Palette.muted)
        percentage.identifier = NSUserInterfaceItemIdentifier("transcription.progress")
        // "—" → "7%" → "100%" changes the label's natural width; a fixed slot keeps
        // the bar and the log button from sliding while the job runs.
        percentage.alignment = .right
        percentage.widthAnchor.constraint(equalToConstant: 40).isActive = true
        progressPercentage = percentage
        let log = button("Журнал обработки", action: #selector(showProcessingLog(_:)), height: 30)
        log.identifier = NSUserInterfaceItemIdentifier("transcription.log")
        log.setContentHuggingPriority(.required, for: .horizontal)
        log.setContentCompressionResistancePriority(.required, for: .horizontal)
        let title = label("Общий прогресс", size: 14, weight: .medium, color: Palette.ink)
        title.setContentHuggingPriority(.required, for: .horizontal)
        title.setContentCompressionResistancePriority(.required, for: .horizontal)
        let row = horizontal([title, progress, percentage, log], spacing: 16)
        row.alignment = .centerY
        let status = wrappedLabel("", size: 12, color: Palette.body)
        status.maximumNumberOfLines = 3
        status.identifier = NSUserInterfaceItemIdentifier("transcription.status")
        progressStatus = status
        let body = vertical([row, status], spacing: 8)
        embed(body, in: view.contentView, inset: 20)
        body.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -20).isActive = true
        refreshProgress()
        return view
    }

    static let speakerCountValues = ["Авто", "1", "2", "3", "4", "5", "6"]

    func speakerCountPopup() -> NSPopUpButton {
        let control = popup(Self.speakerCountValues, key: "processing.speakers")
        control.toolTip = L10n.text("Sortformer определяет спикеров автоматически (до 4); ручное значение доступно для pyannote и ONNX.")
        return control
    }

    /// Sortformer infers the speaker set itself (up to 4); a manual count is only
    /// meaningful for pyannote and ONNX clustering.
    var manualSpeakerCountAvailable: Bool {
        enabledOption("settings.diarization", defaultValue: false)
            && option("settings.diarizationEngine", values: SettingsSchema.diarizationEngines) != "sortformer"
    }

    /// Mirrors the PyQt client: a count hidden behind a disabled control must not
    /// resurface when the engine or the diarization toggle changes again.
    func resetManualSpeakerCountIfUnavailable() {
        guard !manualSpeakerCountAvailable else { return }
        defaults.removeObject(forKey: "processing.speakers")
    }

    var outputFormats: [String] {
        let diarization = enabledOption("settings.diarization", defaultValue: false)
        let choices: [(String, String, Bool)] = [
            ("output.txt", "txt", true), ("output.timestamps", "txt_timecodes", true),
            ("output.md", "md", false), ("output.srt", "srt", false), ("output.vtt", "vtt", false)
        ] + (diarization ? [("output.diarize", "txt_diarize", false), ("output.diarizeTimestamps", "txt_diarize_timecodes", false)] : [])
        return choices.compactMap { key, format, fallback in
            enabledOption(key, defaultValue: fallback) ? format : nil
        }
    }

    func transcriptionSettings() -> NativeTranscriptionSettings {
        var settings = NativeTranscriptionSettings()
        settings.formats = outputFormats
        settings.backend = option("settings.backend", values: SettingsSchema.backends)
        settings.model = option("settings.model", values: SettingsSchema.models)
        settings.onnxProvider = option("settings.onnxProvider", values: SettingsSchema.onnxProviders)
        settings.diarization = enabledOption("settings.diarization", defaultValue: false)
        settings.diarizationBackend = option("settings.diarizationEngine", values: SettingsSchema.diarizationEngines)
        settings.numSpeakers = manualSpeakerCountAvailable ? Int(option("processing.speakers", values: Self.speakerCountValues)) : nil
        settings.audioPreprocessingMode = option("processing.preprocessing", values: SettingsSchema.audioPreprocessing)
        settings.subtitleSentenceSplit = enabledOption("subtitle.sentences", defaultValue: true)
        settings.subtitleMaxLines = Int(option("subtitle.lines", values: SettingsSchema.subtitleLines)) ?? 2
        settings.subtitleMaxWidth = Int(option("subtitle.characters", values: SettingsSchema.subtitleCharacters)) ?? 64
        return settings
    }

    /// The HF token from the Keychain; nil when none is stored (the worker then
    /// uses HF_TOKEN from the environment). Throws when the Keychain cannot be read.
    func storedHFToken() throws -> String? {
        do {
            let token = (try SecureStore.string(for: "hfToken") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return token.isEmpty ? nil : token
        } catch {
            throw WorkerFailure(L10n.format("Не удалось прочитать HF Token из Связки ключей: %@", error.localizedDescription))
        }
    }

    /// The field persists on every keystroke, so a cleared field stores "" rather
    /// than nil; an empty path means "next to the source file", not a default folder.
    var outputPathText: String {
        (defaults.string(forKey: "output.path") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Where results go: `.besideSource` for an empty field, `.folder` for a usable
    /// path, `nil` for a path that is not absolute or not writable.
    enum OutputDestination {
        case besideSource
        case folder(URL)

        var folder: URL? {
            if case .folder(let url) = self { return url }
            return nil
        }
    }

    var outputDestination: OutputDestination? {
        outputPathText.isEmpty ? .besideSource : outputDirectory.map(OutputDestination.folder)
    }

    var outputDirectory: URL? {
        let path = (outputPathText as NSString).expandingTildeInPath
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

    func refreshProcessingControls() {
        let busy = transcriptionJob != nil || mediaDownloadJob != nil || liveJob != nil || isClosing
        func update(_ view: NSView) {
            if let control = view as? NSControl, let key = control.identifier?.rawValue {
                if key.hasPrefix("processing.") || key.hasPrefix("output.") || key.hasPrefix("subtitle.") || ["settings.backend", "settings.model", "settings.onnxProvider", "settings.diarization", "settings.diarizationEngine", "settings.hfToken"].contains(key) {
                    control.isEnabled = !busy
                    if key == "processing.speakers" {
                        control.isEnabled = !busy && manualSpeakerCountAvailable
                        if !manualSpeakerCountAvailable, let popup = control as? NSPopUpButton { popup.selectItem(withTitle: "Авто") }
                    }
                    if ["settings.diarizationEngine", "output.diarize", "output.diarizeTimestamps"].contains(key) { control.isEnabled = !busy && enabledOption("settings.diarization", defaultValue: false) }
                }
            }
            view.subviews.forEach(update)
        }
        if let root = window.contentView { update(root) }
        let reason: String
        if transcriptionJob != nil { reason = "Настройки зафиксированы до завершения обработки. Навигация доступна." }
        else if mediaDownloadJob != nil { reason = "Дождитесь завершения импорта медиа." }
        else if liveJob != nil { reason = "Дождитесь завершения live-сессии." }
        else if selectedFileURLs.isEmpty { reason = "Добавьте аудио или видео для начала обработки." }
        else if outputDestination == nil { reason = "Укажите абсолютный путь к доступной папке результатов или оставьте поле пустым." }
        else if outputFormats.isEmpty { reason = "Выберите хотя бы один формат вывода." }
        else { reason = "" }
        startProcessingButton?.isEnabled = !busy && reason.isEmpty
        startProcessingButton?.toolTip = reason.isEmpty ? nil : L10n.text(reason)
        processingValidationLabel?.stringValue = L10n.text(reason)
        processingValidationLabel?.isHidden = reason.isEmpty
        cancelProcessingButton?.isEnabled = transcriptionJob != nil && !cancellationRequested && !isClosing
        cancelProcessingButton?.title = L10n.text(cancellationRequested ? "Остановка запрошена" : "Остановить после текущего файла")
        // Live recording is gated on the same jobs (batch, media import); its
        // controls were refreshed only from Live's own paths and stayed disabled
        // after a batch or an import had finished.
        refreshLiveControls()
    }

    func refreshProgress() {
        progressTrack?.fraction = transcriptionProgress ?? 0
        progressPercentage?.stringValue = transcriptionProgress.map { "\(Int($0 * 100))%" } ?? "—"
        let status = cancellationRequested && transcriptionJob != nil ? L10n.text("Остановка после текущего файла.") + " " + L10n.text(transcriptionStatus) : L10n.text(transcriptionStatus)
        progressStatus?.stringValue = status
        progressStatus?.toolTip = status
        progressStatus?.invalidateIntrinsicContentSize()
    }

    @objc func startProcessing(_ sender: Any?) {
        window.makeFirstResponder(nil)
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil else { return }
        var settings = transcriptionSettings()
        guard !selectedFileURLs.isEmpty, !settings.formats.isEmpty, let destination = outputDestination else {
            showNotice("Не удалось начать обработку", "Выберите файлы, доступную папку и хотя бы один формат.")
            return
        }
        do { settings.hfToken = try storedHFToken() } catch {
            showNotice("Не удалось начать обработку", error.localizedDescription)
            return
        }
        // The worker's own rule (find_output_collisions): same normalised stem in the
        // same target folder. Beside the source, /a/x.mp3 and /b/x.mp3 do not collide.
        let collisions = OutputNaming.collisions(selectedFileURLs, outputDirectory: destination.folder)
        if !collisions.isEmpty {
            let names = collisions.flatMap { $0.map(\.lastPathComponent) }.sorted().joined(separator: ", ")
            showNotice(
                "Не удалось начать обработку",
                L10n.format("Файлы с одинаковым базовым именем перезапишут результаты друг друга: %@. Переименуйте файлы или обработайте их отдельно.", names)
            )
            return
        }
        if let folder = destination.folder {
            do {
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            } catch {
                showNotice("Не удалось начать обработку", error.localizedDescription)
                return
            }
        }
        transcriptionResults.removeAll()
        selectedResultURL = nil
        transcriptionFiles = selectedFileURLs.map(\.standardizedFileURL)
        for file in transcriptionFiles { fileStates[file] = "В очереди" }
        cancellationRequested = false
        transcriptionProgress = nil
        transcriptionStatus = "Запуск распознавания…"
        processingLog.clear()
        let job = NativeTranscriptionJob(files: transcriptionFiles, outputDirectory: destination.folder, settings: settings) { [weak self] event in
            self?.receiveTranscriptionEvent(event)
        }
        transcriptionJob = job
        refreshSelectedFiles()
        refreshProgress()
        job.start()
    }

    @objc func cancelProcessing(_ sender: Any?) {
        guard let job = transcriptionJob, !cancellationRequested else { return }
        cancellationRequested = true
        job.cancel()
        refreshProcessingControls()
        refreshProgress()
    }

    /// The worker already logs in plain Russian (src.core.processor); the English
    /// UI translates it with the table shared with PyQt (LogTranslation.swift,
    /// generated from src/core/log_i18n.py).
    func appendProcessingLog(_ message: String) {
        appendLogLine(L10n.isEnglish ? LogTranslation.englishText(message) : message)
    }

    /// A line that is already in the UI language (job errors, client notes).
    /// Every writer goes through here or appendProcessingLog: direct appends to
    /// the log string skipped the size cap.
    func appendLogLine(_ line: String) {
        processingLog.append(line)
    }

    func receiveTranscriptionEvent(_ event: NativeTranscriptionEvent) {
        switch event {
        case .log(let message):
            appendProcessingLog(message)
        case .fileStarted(let file, let index, let total):
            fileStates[file.standardizedFileURL] = "В обработке"
            transcriptionStatus = "\(index + 1)/\(total) · \(file.lastPathComponent)"
            refreshSelectedFiles()
            refreshProgress()
        case .progress(let value, let message):
            // nil means "unchanged": an indeterminate stage must not drop the bar to "—".
            if let value { transcriptionProgress = value }
            if !message.isEmpty { transcriptionStatus = message }
            refreshProgress()
        case .fileCompleted(let result):
            fileStates[result.inputURL.standardizedFileURL] = result.error == nil ? "Готово" : "Ошибка"
            if let index = transcriptionResults.firstIndex(where: { $0.inputURL == result.inputURL }) { transcriptionResults[index] = result }
            else { transcriptionResults.append(result) }
            if selectedResultURL == nil { selectedResultURL = result.inputURL }
            if let error = result.error { appendLogLine("\(result.inputURL.lastPathComponent): \(error)") }
            refreshSelectedFiles()
            if !isClosing, currentPage == .result { show(page: .result) }
        case .completed(let success, let cancelled):
            finishTranscription(status: cancelled ? "Обработка остановлена. Готовые результаты сохранены." : (success ? "Обработка завершена. Результаты доступны в разделе «Результат»." : "Обработка завершена с ошибками. Подробности — в журнале обработки."), pendingState: cancelled ? "Не обработан: остановлено" : "Не обработан")
            if success && !cancelled { transcriptionProgress = 1 }
            refreshProgress()
        case .failed(let message):
            appendLogLine(message)
            finishTranscription(status: message, pendingState: "Не обработан: ошибка")
        }
    }

    func finishTranscription(status: String, pendingState: String) {
        transcriptionJob = nil
        transcriptionStatus = status
        for file in transcriptionFiles where fileStates[file] == "В очереди" || fileStates[file] == "В обработке" { fileStates[file] = pendingState }
        if isTerminating { replyWhenJobsFinished(); return }
        guard !isClosing else { return }
        refreshSelectedFiles()
        refreshProgress()
    }

    @objc func showProcessingLog(_ sender: Any?) {
        let alert = NSAlert()
        alert.messageText = L10n.text("Журнал обработки")
        alert.addButton(withTitle: L10n.text("Понятно"))
        let editor = textEditor(processingLog.isEmpty ? L10n.text(transcriptionStatus) : processingLog.text, key: nil, height: 300)
        editor.frame = NSRect(x: 0, y: 0, width: 650, height: 300)
        if let text = editor.documentView as? NSTextView {
            text.isEditable = false
            text.setSelectedRange(NSRange(location: 0, length: 0))
        }
        alert.accessoryView = editor
        alert.beginSheetModal(for: window)
        DispatchQueue.main.async { [weak alert] in
            guard let alert, alert.window.isVisible else { return }
            alert.window.layoutIfNeeded()
            editor.contentView.scroll(to: .zero)
            editor.reflectScrolledClipView(editor.contentView)
        }
    }

    @objc func chooseFiles(_ sender: Any?) {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil else { return }
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        // A chosen folder is scanned recursively, like a dropped one.
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowedContentTypes = [.audio, .movie, .folder]
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let self else { return }
            guard self.transcriptionJob == nil, !self.isClosing else { return }
            self.appendSelectedFiles(MediaScan.expand(panel.urls))
        }
    }

    /// Shared by the open panel and drag & drop: normalises paths and skips duplicates.
    func appendSelectedFiles(_ urls: [URL]) {
        var known = Set(selectedFileURLs.map { $0.standardizedFileURL.resolvingSymlinksInPath() })
        selectedFileURLs.append(contentsOf: urls.map { $0.standardizedFileURL.resolvingSymlinksInPath() }.filter { known.insert($0).inserted })
        refreshSelectedFiles()
    }

    func acceptDroppedFiles(_ urls: [URL]) -> Bool {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil, window.attachedSheet == nil else { return false }
        // Same rules as the PyQt client: a dropped folder is scanned recursively for
        // media by extension; documents are ignored, not rejected loudly.
        let media = MediaScan.expand(urls)
        guard !media.isEmpty else { return false }
        if currentPage != .processing { show(page: .processing) }
        appendSelectedFiles(media)
        return true
    }

    @objc func clearFiles(_ sender: Any?) {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil else { return }
        selectedFileURLs.removeAll()
        cleanupDownloadedMedia()
        refreshSelectedFiles()
    }

    @objc func removeSelectedFile(_ sender: NSButton) {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil, liveJob == nil,
              selectedFileURLs.indices.contains(sender.tag) else { return }
        selectedFileURLs.remove(at: sender.tag)
        if selectedFileURLs.isEmpty { cleanupDownloadedMedia() }
        refreshSelectedFiles()
    }

    @objc func chooseOutputFolder(_ sender: Any?) {
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

    /// Колонки списка выбранных файлов: номер, имя, состояние. Оставляем место
    /// справа для кнопки удаления, не смещая состояние относительно заголовка.
    /// The name column takes whatever width the card has.
    var selectedFileColumns: [(String, CGFloat?)] { [("№", 28), ("Файл", nil), ("Состояние", 200)] }

    static let selectedFileRemoveWidth: CGFloat = 22

    func selectedFileRow(index: Int, url: URL) -> NSView {
        let number = label("\(index + 1).", size: 14, color: Palette.muted)
        number.alignment = .right
        number.widthAnchor.constraint(equalToConstant: selectedFileColumns[0].1 ?? 0).isActive = true
        let name = label(url.lastPathComponent, size: 14, color: Palette.body)
        name.lineBreakMode = .byTruncatingMiddle
        name.toolTip = url.path
        name.setContentHuggingPriority(.defaultLow - 1, for: .horizontal)
        let stateText = fileStates[url.standardizedFileURL] ?? "выбран, не обработан"
        let state = label(stateText, size: 14, color: stateText == "Ошибка" ? Palette.ink : Palette.body)
        state.widthAnchor.constraint(equalToConstant: selectedFileColumns[2].1 ?? 0).isActive = true
        let remove = NSButton(image: NSImage(systemSymbolName: "xmark", accessibilityDescription: nil) ?? NSImage(),
                              target: self, action: #selector(removeSelectedFile(_:)))
        remove.isBordered = false
        remove.imagePosition = .imageOnly
        remove.imageScaling = .scaleProportionallyDown
        remove.contentTintColor = Palette.body
        remove.tag = index
        remove.identifier = NSUserInterfaceItemIdentifier("processing.selected.remove")
        remove.isEnabled = !isClosing && transcriptionJob == nil && mediaDownloadJob == nil && liveJob == nil
        remove.setAccessibilityLabel(L10n.text("Убрать файл") + ": " + url.lastPathComponent)
        remove.toolTip = L10n.text("Убрать файл")
        remove.widthAnchor.constraint(equalToConstant: Self.selectedFileRemoveWidth).isActive = true
        remove.heightAnchor.constraint(equalToConstant: 22).isActive = true
        let row = horizontal([number, name, state, remove], spacing: 8)
        row.alignment = .centerY
        row.setAccessibilityLabel("\(index + 1). \(url.lastPathComponent) — \(L10n.text(stateText))")
        return row
    }

    func refreshSelectedFiles() {
        refreshProcessingControls()
        let empty = selectedFileURLs.isEmpty
        selectedFilesCountLabel?.stringValue = empty ? "" : FileCount.text(selectedFileURLs.count, english: L10n.isEnglish)
        if let rows = selectedFilesRows {
            rows.arrangedSubviews.forEach { $0.removeFromSuperview() }
            if empty, let placeholder = selectedFilesLabel {
                rows.addArrangedSubview(placeholder)
            }
            for (index, url) in selectedFileURLs.enumerated() {
                rows.addArrangedSubview(selectedFileRow(index: index, url: url))
            }
        }
        if let document = selectedFilesRows?.enclosingScrollView?.documentView {
            document.needsLayout = true
            document.layoutSubtreeIfNeeded()
        }
    }
}
