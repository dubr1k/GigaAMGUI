import AppKit
import GigaAMLiquidCore

/// The Live page: capture sources, the live session job, its transcript and
/// the assistant's questions.
extension AppController {
    private static let liveDiarizationModes = ["Выкл.", "Оценка вживую", "После остановки"]

    private static let liveDiarizationModeValues = ["off", "live_estimate", "after_stop"]

    func buildLive(into content: NSStackView) {
        let source = card("Источник аудио", dense: true)
        let sourceBody = contentStack(source)
        sourceBody.spacing = 5
        let devices = MicrophoneCapture.devices()
        liveDeviceIDs = ["default"] + devices.map(\.id)
        let microphone = popup([L10n.text("По умолчанию")] + devices.map(\.name), key: "live.microphone")
        if let stored = defaults.string(forKey: "live.microphone"), let index = liveDeviceIDs.firstIndex(of: stored) { microphone.selectItem(at: index) }
        sourceBody.addArrangedSubview(compactField("Микрофон", control: microphone))
        sourceBody.addArrangedSubview(toggleRow("Системный звук", key: "live.systemAudio", defaultValue: false))
        sourceBody.addArrangedSubview(toggleRow("Записывать микрофон", key: "live.recordMic", defaultValue: true))
        sourceBody.addArrangedSubview(toggleRow("Записывать системный звук", key: "live.recordSystem", defaultValue: false))
        sourceBody.addArrangedSubview(compactField("Диаризация", control: popup(Self.liveDiarizationModes, key: "live.diarizationMode")))
        sourceBody.addArrangedSubview(compactField("Движок", control: popup(SettingsSchema.diarizationEngines, key: "live.diarizationEngine")))
        source.heightAnchor.constraint(equalToConstant: 306).isActive = true

        let recorder = card("Запись")
        let recorderBody = contentStack(recorder)
        recorderBody.spacing = 9
        let clock = label("00:00:00", size: 28, weight: .medium, color: Palette.ink)
        clock.identifier = NSUserInterfaceItemIdentifier("live.clock")
        liveClockLabel = clock
        recorderBody.addArrangedSubview(centered(clock))
        let status = wrappedLabel("Запись не начата", size: 12, color: Palette.body)
        status.identifier = NSUserInterfaceItemIdentifier("live.status")
        status.maximumNumberOfLines = 3
        status.alignment = .center
        liveStatusLabel = status
        recorderBody.addArrangedSubview(status)
        // Where the transcript goes, always in view: before recording the root and
        // the folder name pattern, then the actual session folder.
        let folderPath = wrappedLabel("", size: 11, color: Palette.muted)
        folderPath.identifier = NSUserInterfaceItemIdentifier("live.folderPath")
        folderPath.maximumNumberOfLines = 2
        folderPath.alignment = .center
        // Caption and path on their own lines; a long path loses its middle,
        // never its tail (the session folder name).
        folderPath.lineBreakMode = .byTruncatingMiddle
        folderPath.isSelectable = true
        liveFolderLabel = folderPath
        recorderBody.addArrangedSubview(folderPath)
        let meter = ProgressTrackView()
        meter.heightAnchor.constraint(equalToConstant: 8).isActive = true
        meter.setAccessibilityLabel(L10n.text("Уровень сигнала"))
        liveLevelView = meter
        recorderBody.addArrangedSubview(meter)
        let start = iconButton("record.circle", hint: "Начать запись", action: #selector(startLive(_:)))
        start.identifier = NSUserInterfaceItemIdentifier("live.start")
        liveStartButton = start
        let pause = iconButton("pause.fill", hint: "Приостановить запись", action: #selector(pauseLive(_:)))
        pause.identifier = NSUserInterfaceItemIdentifier("live.pause")
        livePauseButton = pause
        let stop = iconButton("stop.fill", hint: "Остановить запись", action: #selector(stopLive(_:)))
        stop.identifier = NSUserInterfaceItemIdentifier("live.stop")
        liveStopButton = stop
        let reveal = iconButton("folder", hint: "Открыть папку сессии", action: #selector(revealLiveSession(_:)))
        reveal.identifier = NSUserInterfaceItemIdentifier("live.reveal")
        recorderBody.addArrangedSubview(centered(horizontal([start, pause, stop, reveal], spacing: 8)))
        recorder.heightAnchor.constraint(equalToConstant: 306).isActive = true

        let parameters = card("Параметры", dense: true)
        let parametersBody = contentStack(parameters)
        parametersBody.spacing = 5
        parametersBody.addArrangedSubview(equalColumns([
            checkbox("TXT (.txt)", key: "live.txt", defaultValue: true),
            checkbox("Таймкоды", key: "live.timestamps", defaultValue: false)
        ], spacing: 8))
        parametersBody.addArrangedSubview(equalColumns([
            checkbox("Markdown", key: "live.md", defaultValue: false),
            checkbox("SRT (.srt)", key: "live.srt", defaultValue: true)
        ], spacing: 8))
        parametersBody.addArrangedSubview(checkbox("VTT (.vtt)", key: "live.vtt", defaultValue: false))
        // The diarization titles do not fit a half-width column of this card
        // ("Диар. + таймкоды" was truncated to "Диар. +"); give them full rows
        // like the processing page does.
        parametersBody.addArrangedSubview(checkbox("Диаризация (.txt)", key: "live.diarize", defaultValue: false))
        parametersBody.addArrangedSubview(checkbox("Диар. + таймкоды", key: "live.diarizeTimestamps", defaultValue: false))
        parametersBody.addArrangedSubview(equalColumns([
            compactField("Строк в блоке", control: popup(SettingsSchema.subtitleLines, key: "live.lines")),
            compactField("Символов", control: popup(SettingsSchema.subtitleCharacters, key: "live.characters"))
        ], spacing: 8))
        parametersBody.addArrangedSubview(toggleRow("Разбивать по предложениям", key: "live.sentences", defaultValue: true))
        let folder = editableText(liveSessionRootText, key: "live.sessionRoot", placeholder: "Папка сессий")
        folder.font = NSFont.systemFont(ofSize: 12)
        folder.setContentHuggingPriority(.defaultLow, for: .horizontal)
        liveRootField = folder
        let chooseRoot = button("Выбрать", action: #selector(chooseLiveSessionRoot(_:)), height: 30)
        chooseRoot.identifier = NSUserInterfaceItemIdentifier("live.chooseRoot")
        chooseRoot.widthAnchor.constraint(equalToConstant: 76).isActive = true
        parametersBody.addArrangedSubview(compactField("Папка сессий", control: horizontal([folder, chooseRoot], spacing: 6)))
        size(parameters, width: 270, height: 306)
        content.addArrangedSubview(horizontal([source, recorder, parameters], spacing: 16))
        // Source and recorder share the width left after the fixed parameters card.
        recorder.widthAnchor.constraint(equalTo: source.widthAnchor).isActive = true
        refreshLiveFolderLabel()

        let transcript = card("Live transcript")
        let transcriptBody = contentStack(transcript)
        transcriptBody.spacing = 10
        let editor = stretchy(textEditor("", key: nil, height: nil, minHeight: 120))
        liveTranscriptView = editor.documentView as? NSTextView
        liveTranscriptRenderer.reset()  // a new view: render everything once
        liveTranscriptView?.isEditable = false
        liveTranscriptView?.identifier = NSUserInterfaceItemIdentifier("live.transcript")
        liveTranscriptView?.setAccessibilityLabel(L10n.text("Live transcript"))
        transcriptBody.addArrangedSubview(editor)
        renderLiveTranscript()
        let question = editableText("", key: "live.question", placeholder: "Вопрос ассистенту по текущей записи")
        question.heightAnchor.constraint(equalToConstant: 36).isActive = true
        question.setContentHuggingPriority(.defaultLow, for: .horizontal)
        liveQuestionField = question
        let ask = button("Спросить", primary: true, action: #selector(askLive(_:)), height: 36)
        ask.identifier = NSUserInterfaceItemIdentifier("live.ask")
        ask.widthAnchor.constraint(equalToConstant: 120).isActive = true
        liveAskButton = ask
        // Not «Отменить»: that key is the Edit menu's Undo.
        let cancelAsk = button("Отменить вопрос", action: #selector(cancelAskLive(_:)), height: 36)
        cancelAsk.identifier = NSUserInterfaceItemIdentifier("live.askCancel")
        cancelAsk.widthAnchor.constraint(equalToConstant: 160).isActive = true
        liveAskCancelButton = cancelAsk
        transcriptBody.addArrangedSubview(horizontal([question, ask, cancelAsk], spacing: 12))
        let answer = textEditor(liveAnswerText, key: nil, height: 96)
        liveAnswerView = answer.documentView as? NSTextView
        liveAnswerView?.isEditable = false
        liveAnswerView?.identifier = NSUserInterfaceItemIdentifier("live.answer")
        liveAnswerView?.setAccessibilityLabel(L10n.text("Ответ ассистента"))
        transcriptBody.addArrangedSubview(answer)
        transcriptBody.bottomAnchor.constraint(equalTo: transcript.bottomAnchor, constant: -16).isActive = true
        content.addArrangedSubview(stretchy(transcript))
        refreshLiveControls()
        refreshLiveClock()
    }

    // MARK: - Session

    private var liveExports: [String: Any] {
        [
            "txt": enabledOption("live.txt", defaultValue: true),
            "txt_timecodes": enabledOption("live.timestamps", defaultValue: false),
            "txt_diarize": enabledOption("live.diarize", defaultValue: false),
            "txt_diarize_timecodes": enabledOption("live.diarizeTimestamps", defaultValue: false),
            "md": enabledOption("live.md", defaultValue: false),
            "srt": enabledOption("live.srt", defaultValue: true),
            "vtt": enabledOption("live.vtt", defaultValue: false),
            "sentence_split": enabledOption("live.sentences", defaultValue: true),
            "max_line_count": Int(option("live.lines", values: SettingsSchema.subtitleLines)) ?? 2,
            "max_line_width": Int(option("live.characters", values: SettingsSchema.subtitleCharacters)) ?? 64
        ]
    }

    /// Like `outputPathText`: an empty field means the default folder.
    private var liveSessionRootText: String {
        let stored = (defaults.string(forKey: "live.sessionRoot") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return stored.isEmpty ? "~/Documents/GigaAM/live" : stored
    }

    private var liveSessionRoot: URL? {
        let path = (liveSessionRootText as NSString).expandingTildeInPath
        guard path.hasPrefix("/"), !path.contains("\0") else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL
    }

    @objc private func startLive(_ sender: Any?) {
        window.makeFirstResponder(nil)
        if liveState == "paused" { liveJob?.resume(); return }
        guard liveJob == nil, transcriptionJob == nil, mediaDownloadJob == nil, llmJob == nil, !isClosing else {
            showNotice("Не удалось начать запись", "Дождитесь завершения текущей обработки.")
            return
        }
        guard let root = liveSessionRoot else { showNotice("Не удалось начать запись", "Укажите абсолютный путь к папке сессий."); return }
        do { try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true) }
        catch { showNotice("Не удалось начать запись", error.localizedDescription); return }
        let wantsSystem = enabledOption("live.systemAudio", defaultValue: false)
        liveStatusLabel?.stringValue = L10n.text("Запрос доступа к микрофону…")
        MicrophoneCapture.requestAccess { [weak self] granted in
            guard let self, !self.isClosing, self.liveJob == nil else { return }
            guard granted else {
                self.liveStatusLabel?.stringValue = L10n.text("Запись не начата")
                self.showNotice("Нет доступа к микрофону", "Разрешите доступ в Системных настройках → Конфиденциальность и безопасность → Микрофон.")
                return
            }
            if wantsSystem, !SystemAudioCapture.requestAccess() {
                self.liveStatusLabel?.stringValue = L10n.text("Запись не начата")
                self.showNotice("Нет доступа к системному звуку", "Разрешите «Запись экрана и системного звука» для GigaAMLiquid в Системных настройках → Конфиденциальность и безопасность.")
                return
            }
            self.launchLive(root: root, withSystem: wantsSystem)
        }
    }

    private func launchLive(root: URL, withSystem: Bool) {
        var settings = LiveSessionSettings(sessionRoot: root, sources: withSystem ? [.mic, .system] : [.mic])
        let device = defaults.string(forKey: "live.microphone") ?? "default"
        settings.microphoneDeviceID = device == "default" ? nil : device
        let modeIndex = Self.liveDiarizationModes.firstIndex(of: option("live.diarizationMode", values: Self.liveDiarizationModes)) ?? 0
        settings.diarizationMode = Self.liveDiarizationModeValues[modeIndex]
        settings.diarizationBackend = option("live.diarizationEngine", values: SettingsSchema.diarizationEngines)
        settings.recordMic = enabledOption("live.recordMic", defaultValue: true)
        settings.recordSystem = enabledOption("live.recordSystem", defaultValue: false)
        settings.exports = liveExports
        settings.backend = option("settings.backend", values: SettingsSchema.backends)
        settings.model = option("settings.model", values: SettingsSchema.models)
        settings.onnxProvider = option("settings.onnxProvider", values: SettingsSchema.onnxProviders)
        do { settings.hfToken = try storedHFToken() } catch {
            liveStatusLabel?.stringValue = L10n.text("Запись не начата")
            showNotice("Не удалось начать запись", error.localizedDescription)
            return
        }
        // Captures forward audio to the job, which owns them: the job hands them a
        // sink that holds it weakly, so a finished session (worker, AVAudioEngine) is freed.
        let microphoneID = settings.microphoneDeviceID
        let created = LiveSessionJob(settings: settings, makeCaptures: { forward in
            var captures: [LiveCaptureSource] = [MicrophoneCapture(deviceID: microphoneID, onEvent: forward)]
            if withSystem { captures.append(SystemAudioCapture(onEvent: forward)) }
            return captures
        }, onEvent: { [weak self] event in self?.receiveLiveEvent(event) })
        liveJob = created
        liveSessionDir = nil
        refreshLiveFolderLabel()
        liveFinals = []
        liveFinalsRevision += 1
        livePartials = [:]
        liveAnswerText = ""
        liveAnswerView?.string = ""
        // The clock starts with capture, once the model is loaded.
        liveStartedAt = nil
        refreshLiveClock()
        liveTimer?.invalidate()
        liveTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.refreshLiveClock() }
        liveState = "starting"
        liveStatusLabel?.stringValue = L10n.text("Запуск…")
        processingLog.clear()
        refreshLiveControls()
        refreshProcessingControls()
        renderLiveTranscript()
        created.start()
    }

    func refreshLiveFolderLabel() {
        func short(_ url: URL) -> String { (url.path as NSString).abbreviatingWithTildeInPath }
        let text: String
        if let directory = liveSessionDir {
            text = L10n.text(liveJob == nil ? "Сохранено в " : "Запись в ") + "\n" + short(directory)
        } else if let root = liveSessionRoot {
            text = L10n.text("Сохраняется в ") + "\n" + short(root)
        } else {
            text = L10n.text("Укажите абсолютный путь к папке сессий.")
        }
        liveFolderLabel?.stringValue = text
        let hint = L10n.text("Каждая запись сохраняется в свою папку ГГГГ-ММ-ДД_ЧЧ-ММ-СС: транскрипт, субтитры и аудио.")
        liveFolderLabel?.toolTip = [(liveSessionDir ?? liveSessionRoot)?.path, hint].compactMap { $0 }.joined(separator: "\n")
    }

    @objc private func revealLiveSession(_ sender: Any?) {
        if let directory = liveSessionDir, FileManager.default.fileExists(atPath: directory.path) {
            NSWorkspace.shared.open(directory)
            return
        }
        guard let root = liveSessionRoot else {
            showNotice("Не удалось открыть папку", "Укажите абсолютный путь к папке сессий.")
            return
        }
        do { try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true) }
        catch { showNotice("Не удалось открыть папку", error.localizedDescription); return }
        NSWorkspace.shared.open(root)
    }

    @objc private func chooseLiveSessionRoot(_ sender: Any?) {
        guard liveJob == nil, !isClosing else { return }
        // A focused root field would write its old text back over the pick when
        // it ends editing.
        window.makeFirstResponder(nil)
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = liveSessionRoot
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url, let self else { return }
            self.defaults.set(url.path, forKey: "live.sessionRoot")
            self.liveRootField?.stringValue = (url.path as NSString).abbreviatingWithTildeInPath
            self.liveSessionDir = nil
            self.refreshLiveFolderLabel()
        }
    }

    @objc private func pauseLive(_ sender: Any?) { liveJob?.pause() }

    @objc private func stopLive(_ sender: Any?) {
        liveStatusLabel?.stringValue = L10n.text("Остановка…")
        liveJob?.stop()
    }

    @objc private func askLive(_ sender: Any?) {
        guard let job = liveJob else { return }
        let question = (liveQuestionField?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return }
        let settings: [String: Any]
        do { settings = try llmSettings() } catch { showNotice("LLM не настроена", error.localizedDescription); return }
        guard !liveAsking else { return }
        liveAsking = true
        liveAnswerText = ""
        liveAnswerView?.string = L10n.text("Ассистент отвечает…")
        refreshLiveControls()
        job.ask(question, settings: settings)
    }

    @objc private func cancelAskLive(_ sender: Any?) { liveJob?.cancelAsk() }

    private func receiveLiveEvent(_ event: LiveSessionEvent) {
        switch event {
        case .loading:
            liveStatusLabel?.stringValue = L10n.text("Загрузка модели распознавания… Запись начнётся, когда она будет готова.")
        case .status(let state, _, let failed, let sessionDir):
            liveState = state
            if state == "recording", liveStartedAt == nil {
                liveStartedAt = Date()
                refreshLiveClock()
            }
            if let sessionDir, sessionDir != liveSessionDir {
                liveSessionDir = sessionDir
                refreshLiveFolderLabel()
            }
            if !failed.isEmpty {
                liveStatusLabel?.stringValue = L10n.text("Источник недоступен: ") + failed.map(\.rawValue).joined(separator: ", ")
            } else {
                // Every CaptureState of src/live/types.py; an unknown state shows as is.
                let titles = ["idle": "Ожидание", "starting": "Запуск…", "recording": "Идёт запись", "paused": "Пауза",
                              "stopping": "Остановка…", "stopped": "Остановлено", "failed": "Захват не удался"]
                liveStatusLabel?.stringValue = L10n.text(titles[state] ?? state)
            }
            refreshLiveControls()
        case .partial(_, let source, _, let text):
            livePartials[source] = text
            renderLiveTranscript()
        case .final(let id, let source, _, _, let text, let speaker):
            // Only this source's draft is settled; the other source may still be talking.
            livePartials.removeValue(forKey: source)
            let firstFinal = liveFinals.isEmpty
            if let index = liveFinals.firstIndex(where: { $0.id == id }) {
                liveFinals[index] = (id, text, speaker)
                liveFinalsRevision += 1
            } else { liveFinals.append((id, text, speaker)) }
            renderLiveTranscript()
            if firstFinal { refreshLiveControls() }  // the assistant needs at least one final
        case .level(_, let rms):
            liveLevelView?.fraction = Double(min(1, rms * 4))
        case .captureEvent(_, let kind, let detail):
            if kind != "status" { liveStatusLabel?.stringValue = detail }
            appendLogLine("[live/\(kind)] \(detail)")
        case .answerChunk(_, let text):
            // The first chunk replaces "Ассистент отвечает…"; later ones are appended.
            if liveAnswerText.isEmpty { liveAnswerView?.string = text } else { liveAnswerView?.appendStreamed(text) }
            liveAnswerText += text
            scrollToTail(liveAnswerView)
        case .answer(_, let status, let text):
            switch status {
            case "complete": liveAnswerText = text
            case "cancelled": liveAnswerText = L10n.text("Запрос отменён.")
            // The worker refused the question itself (asked before the first phrase, …).
            case "rejected": liveAnswerText = L10n.text("Ассистент не ответил: ") + L10n.workerMessage(text)
            default: liveAnswerText = L10n.text("Ошибка LLM: ") + text
            }
            liveAnswerView?.string = liveAnswerText
            liveAsking = false
            refreshLiveControls()
        case .stopped(let directory, let exports, let recordings, let outcome):
            liveSessionDir = directory
            // Every file in the log — each recording segment of a long session too;
            // the status line names them as far as it reaches.
            let saved = exports + recordings
            saved.forEach { appendLogLine(L10n.format("Сохранён файл: %@", $0.path)) }
            let names = saved.map(\.lastPathComponent).joined(separator: ", ")
            let list = names.isEmpty ? "" : " · " + names
            switch outcome {
            case .saved:
                finishLive(status: L10n.text("Сессия сохранена") + list)
            case .savedWithWarning(let message):
                // A failed stage (after-stop diarization, one export) next to saved files.
                finishLive(status: L10n.format("Сессия сохранена, но не полностью: %@", message) + list)
            case .failed(let message):
                finishLive(status: L10n.text("Сессия остановлена с ошибкой: ") + message)
            }
        case .failed(let message):
            appendLogLine(message)
            finishLive(status: message)
        case .log(let message):
            appendProcessingLog(message)
        }
    }

    private func finishLive(status: String) {
        liveJob = nil
        liveAsking = false
        liveState = "idle"
        liveTimer?.invalidate()
        liveTimer = nil
        liveStartedAt = nil
        liveLevelView?.fraction = 0
        liveStatusLabel?.stringValue = status
        // A draft whose final never came (too short, low confidence) is not in
        // the exports; leaving it on screen after stop misrepresented the session.
        livePartials.removeAll()
        renderLiveTranscript()
        refreshLiveFolderLabel()
        // Quitting waited for this session to end.
        let exitHandler = liveExitHandler
        liveExitHandler = nil
        liveExitDeadline?.invalidate()
        liveExitDeadline = nil
        defer { exitHandler?() }
        if isTerminating { replyWhenJobsFinished(); return }
        guard !isClosing else { return }
        refreshLiveControls()
        refreshProcessingControls()
    }

    private func renderLiveTranscript() {
        guard let view = liveTranscriptView, let storage = view.textStorage else { return }
        let finals = liveFinals.map { ($0.speaker.map { "\($0): " } ?? "") + $0.text }
        let drafts = livePartials.sorted(by: { $0.key.rawValue < $1.key.rawValue }).map { "[\($0.key.rawValue) …] \($0.value)" }
        // Appends new finals and swaps the drafts at the end; the whole text is
        // laid out again only when a shown final changes.
        liveTranscriptRenderer.render(finals: finals, revision: liveFinalsRevision, drafts: drafts,
                                      placeholder: L10n.text("Нет фрагментов. Начните запись."),
                                      into: storage, attributes: view.typingAttributes)
        scrollToTail(view)
    }

    private func refreshLiveClock() {
        guard let started = liveStartedAt else { liveClockLabel?.stringValue = "00:00:00"; return }
        let seconds = Int(Date().timeIntervalSince(started))
        liveClockLabel?.stringValue = String(format: "%02d:%02d:%02d", seconds / 3600, seconds % 3600 / 60, seconds % 60)
    }

    func refreshLiveControls() {
        let running = liveJob != nil
        liveStartButton?.isEnabled = !isClosing && (!running || liveState == "paused") && transcriptionJob == nil && mediaDownloadJob == nil && llmJob == nil
        livePauseButton?.isEnabled = running && liveState == "recording"
        liveStopButton?.isEnabled = running && liveState != "stopping"
        liveQuestionField?.isEnabled = running
        // One question at a time: a second one cleared the streaming answer, the
        // worker rejected it, and the old answer's chunks kept arriving.
        liveAskButton?.isEnabled = running && !liveFinals.isEmpty && !liveAsking
        liveAskCancelButton?.isEnabled = running && liveAsking
        func update(_ view: NSView) {
            if let control = view as? NSControl, let key = control.identifier?.rawValue,
               key.hasPrefix("live."), !["live.question", "live.start", "live.pause", "live.stop", "live.ask", "live.askCancel", "live.reveal", "live.folderPath"].contains(key) {
                control.isEnabled = !running
            }
            view.subviews.forEach(update)
        }
        if let root = window.contentView { update(root) }
    }
}
