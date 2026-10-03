import AppKit

/// Importing media from a URL (MediaDownloadJob) and cleaning the app's
/// download cache.
extension AppController {
    @objc func chooseMediaURL(_ sender: Any?) {
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

    func cleanupDownloadedMedia() {
        let manager = FileManager.default
        if let cache = mediaCacheRoot(), downloadedMediaRoots.isEmpty {
            guard manager.fileExists(atPath: cache.path) else { return }
            do {
                try manager.removeItem(at: cache)
            } catch {
                NSLog("GigaAMLiquid: failed to clean media cache %@: %@", cache.path, error.localizedDescription)
            }
            return
        }
        var failed = Set<URL>()
        for root in downloadedMediaRoots {
            guard manager.fileExists(atPath: root.path) else { continue }
            do {
                try manager.removeItem(at: root)
            } catch {
                failed.insert(root)
                NSLog("GigaAMLiquid: failed to remove downloaded media %@: %@", root.path, error.localizedDescription)
            }
        }
        downloadedMediaRoots = failed
    }
}
