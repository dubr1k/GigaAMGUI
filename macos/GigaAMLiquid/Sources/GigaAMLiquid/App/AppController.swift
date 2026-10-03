import AppKit
import GigaAMLiquidCore
import UniformTypeIdentifiers

/// The application delegate and the window's controller. Its state lives here;
/// each page, the shared control factory, media import and persistence are
/// extensions in their own files (Pages/, UI/, MediaImportFlow.swift,
/// Persistence.swift). Members used across those files are internal.
final class AppController: NSObject, NSApplicationDelegate {
    let defaults = UserDefaults.standard
    var window: NSWindow!
    var mainSurface: GlassView!
    var pageTitle: NSTextField!
    var pageSubtitle: NSTextField!
    var pageScroll: NSScrollView!
    var navigationButtons: [Page: NSButton] = [:]
    var selectedFilesLabel: NSTextField?
    var selectedFilesRows: NSStackView?
    var selectedFilesCountLabel: NSTextField?
    var settingsCategoryButtons: [String: NSButton] = [:]
    var currentPage: Page = .processing
    var settingsDetail: NSView?
    var transcriptEditor: NSTextView?
    var promptEditor: NSTextView?
    var apiCodeText: NSTextView?
    var resultTranscript: NSTextView?
    var resultPages: [(key: String, title: String, text: String)] = []
    var selectedResultTab = "transcript"
    var selectedResultURL: URL?
    var selectedOutputFormat: String?
    var transcriptionResults: [NativeTranscriptionResult] = []
    var transcriptionJob: NativeTranscriptionJob?
    var liveJob: LiveSessionJob?
    var liveState = "idle"
    var liveFinals: [(id: String, text: String, speaker: String?)] = []
    var livePartials: [LiveSource: String] = [:]
    var liveStartedAt: Date?
    var liveTimer: Timer?
    var liveAnswerText = ""
    var liveDeviceIDs: [String] = []
    weak var liveTranscriptView: NSTextView?
    weak var liveClockLabel: NSTextField?
    weak var liveStatusLabel: NSTextField?
    weak var liveFolderLabel: NSTextField?
    weak var liveRootField: NSTextField?
    /// The folder of the running or last finished session; nil before the first one.
    var liveSessionDir: URL?
    weak var liveLevelView: ProgressTrackView?
    weak var liveStartButton: NSButton?
    weak var livePauseButton: NSButton?
    weak var liveStopButton: NSButton?
    weak var liveQuestionField: NSTextField?
    weak var liveAskButton: NSButton?
    weak var liveAnswerView: NSTextView?
    var llmJob: LLMJob?
    var llmToolsQuery: LLMToolsQuery?
    var llmToolChecks: [String: LLMToolsQuery] = [:]
    /// Last discovery result per provider name; persisted so the page renders
    /// badges immediately while a fresh scan runs in the worker.
    var llmToolStatuses: [String: LLMToolStatus] = [:]
    weak var llmProviderStatusLabel: NSTextField?
    var llmToolRows: [String: (dot: NSTextField, version: NSTextField, path: NSTextField, check: NSButton)] = [:]
    weak var llmRescanButton: NSButton?
    var llmResultText = ""
    weak var llmResultView: NSTextView?
    weak var llmRunButton: NSButton?
    weak var llmCancelButton: NSButton?
    weak var llmStatusLabel: NSTextField?
    weak var llmCopyButton: NSButton?
    weak var llmSaveButton: NSButton?
    var transcriptionFiles: [URL] = []
    var fileStates: [URL: String] = [:]
    var cancellationRequested = false
    var transcriptionProgress: Double? = 0
    var transcriptionStatus = "Нет активных задач"
    /// The shared processing log (batch, Live, LLM); capped, see LogBuffer.
    var processingLog = LogBuffer()
    weak var processingValidationLabel: NSTextField?
    weak var startProcessingButton: NSButton?
    weak var cancelProcessingButton: NSButton?
    weak var progressTrack: ProgressTrackView?
    weak var progressPercentage: NSTextField?
    weak var progressStatus: NSTextField?
    var selectedFileURLs: [URL] = []
    var downloadedMediaRoots = Set<URL>()
    var mediaDownloadJob: MediaDownloadJob?
    var mediaImportAlert: NSAlert?
    weak var mediaImportButton: NSButton?
    var isClosing = false
    var isTerminating = false
    var searchField: NSSearchField?
    var searchResults = NSView()
    var searchCapsule: GlassView?
    var searchWidth: NSLayoutConstraint?
    var appearanceObservation: NSKeyValueObservation?
    /// Whether the views on screen were built with the dark palette.
    var appliedDarkLook = false
    /// Secrets typed but not yet written to the Keychain, by control identifier.
    var pendingSecrets: [String: String] = [:]
    /// A tool re-check waiting for the user to stop typing its path, by provider.
    var pendingToolChecks: [String: DispatchWorkItem] = [:]

    func applicationDidFinishLaunching(_ notification: Notification) {
        for (old, current) in [("settings.sentences", "subtitle.sentences"), ("settings.subtitleLines", "subtitle.lines"), ("settings.subtitleCharacters", "subtitle.characters")] {
            if defaults.object(forKey: current) == nil, let value = defaults.object(forKey: old) { defaults.set(value, forKey: current) }
            defaults.removeObject(forKey: old)
        }
        if let legacyToken = defaults.string(forKey: "settings.hfToken"), !legacyToken.isEmpty {
            do {
                try SecureStore.set(legacyToken, for: "hfToken")
                defaults.removeObject(forKey: "settings.hfToken")
            } catch {
                NSLog("GigaAMLiquid: HF token migration to Keychain failed: %@", error.localizedDescription)
            }
        } else {
            defaults.removeObject(forKey: "settings.hfToken")
        }
        // «Анимации» was a switch nothing read; the one animation (the search field)
        // follows the system's Reduce Motion setting.
        defaults.removeObject(forKey: "settings.animations")
        cleanupDownloadedMedia()
        loadLLMToolsCache()
        // The palette is baked into the views when they are built; with «Системная»
        // a change of the macOS appearance needs a rebuild to show.
        appearanceObservation = NSApp.observe(\.effectiveAppearance, options: [.new]) { [weak self] _, _ in
            DispatchQueue.main.async {
                guard let self, Palette.followsSystem, Palette.isDark != self.appliedDarkLook else { return }
                self.rebuildInterface(activate: false)
            }
        }
        installMainMenu()
        buildWindow()
        show(page: .processing)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // Single-window utility app: closing the window is quitting.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    /// A SwiftPM executable ships no MainMenu.nib, so without this the menu bar shows only the
    /// app name and ⌘Q/⌘W/⌘C/⌘V have no key equivalents. Standard selectors with a nil target
    /// travel the responder chain (text fields, window, NSApp); own actions target self.
    func installMainMenu() {
        let appName = "GigaAM v3"
        func item(_ title: String, _ action: Selector?, keyEquivalent: String = "", modifiers: NSEvent.ModifierFlags = .command, target: AnyObject? = nil) -> NSMenuItem {
            let item = NSMenuItem(title: L10n.text(title), action: action, keyEquivalent: keyEquivalent)
            item.keyEquivalentModifierMask = modifiers
            item.target = target
            return item
        }
        func submenu(_ title: String, _ items: [NSMenuItem]) -> NSMenuItem {
            let menu = NSMenu(title: L10n.text(title))
            items.forEach(menu.addItem)
            let holder = NSMenuItem(title: L10n.text(title), action: nil, keyEquivalent: "")
            holder.submenu = menu
            return holder
        }

        let main = NSMenu()
        main.addItem(submenu(appName, [
            item("О приложении " + appName, #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            item("Скрыть " + appName, #selector(NSApplication.hide(_:)), keyEquivalent: "h"),
            item("Скрыть остальные", #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h", modifiers: [.command, .option]),
            item("Показать все", #selector(NSApplication.unhideAllApplications(_:))),
            .separator(),
            item("Завершить " + appName, #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        ]))
        main.addItem(submenu("Файл", [
            item("Выбрать файлы…", #selector(chooseFiles(_:)), keyEquivalent: "o", target: self),
            item("Ссылка на медиа…", #selector(chooseMediaURL(_:)), keyEquivalent: "o", modifiers: [.command, .shift], target: self),
            .separator(),
            item("Закрыть окно", #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        ]))
        main.addItem(submenu("Правка", [
            item("Отменить", Selector(("undo:")), keyEquivalent: "z"),
            item("Повторить", Selector(("redo:")), keyEquivalent: "z", modifiers: [.command, .shift]),
            .separator(),
            item("Вырезать", #selector(NSText.cut(_:)), keyEquivalent: "x"),
            item("Копировать", #selector(NSText.copy(_:)), keyEquivalent: "c"),
            item("Вставить", #selector(NSText.paste(_:)), keyEquivalent: "v"),
            item("Выделить всё", #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        ]))
        let windowMenu = submenu("Окно", [
            item("Свернуть", #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m"),
            item("Масштабировать", #selector(NSWindow.performZoom(_:))),
            .separator(),
            item("Все окна — на передний план", #selector(NSApplication.arrangeInFront(_:)))
        ])
        main.addItem(windowMenu)
        let helpMenu = submenu("Справка", [
            item("Проект на GitHub", #selector(openProjectPage(_:)), target: self)
        ])
        main.addItem(helpMenu)
        NSApp.mainMenu = main
        NSApp.windowsMenu = windowMenu.submenu
        NSApp.helpMenu = helpMenu.submenu
    }

    @objc func openProjectPage(_ sender: Any?) {
        NSWorkspace.shared.open(URL(string: "https://github.com/dubr1k/GigaAMGUI")!)
    }

    /// The smallest window in which every page fits without scrolling; the tallest
    /// page, «Обработка», needs ~870 pt of window height. Pages stretch beyond it.
    static let minimumWindowSize = NSSize(width: 1100, height: 880)
    static let defaultWindowSize = NSSize(width: 1240, height: 940)

    func buildWindow() {
        // Clamp to the screen: on a display shorter than the minimum the page falls
        // back to scrolling rather than the window hanging off the screen.
        let screen = NSScreen.main?.visibleFrame.size ?? Self.defaultWindowSize
        let minimum = NSSize(width: min(Self.minimumWindowSize.width, screen.width), height: min(Self.minimumWindowSize.height, screen.height))
        let initial = NSSize(width: min(Self.defaultWindowSize.width, screen.width), height: min(Self.defaultWindowSize.height, screen.height))
        window = ApplicationWindow(contentRect: NSRect(origin: .zero, size: initial), styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.title = "GigaAM v3"
        window.delegate = self
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.isMovableByWindowBackground = true
        window.hasShadow = false
        window.isOpaque = false
        window.backgroundColor = .clear
        window.minSize = minimum
        window.center()
        buildWindowContent()
    }

    func buildWindowContent() {
        navigationButtons.removeAll()
        selectedFilesLabel = nil
        selectedFilesRows = nil
        selectedFilesCountLabel = nil
        settingsCategoryButtons.removeAll()
        // A pinned theme pins the window; «Системная» leaves it to macOS.
        window.appearance = Palette.followsSystem ? nil : NSAppearance(named: Palette.isDark ? .darkAqua : .aqua)
        appliedDarkLook = Palette.isDark
        let background = BlobBackgroundView()
        background.translatesAutoresizingMaskIntoConstraints = false
        background.dropHandler = { [weak self] urls in self?.acceptDroppedFiles(urls) ?? false }
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

    func buildSidebar() -> NSView {
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

    func brandMark() -> NSView {
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

    func navigationButton(for page: Page) -> NSButton {
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

    func buildMainSurface() {
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
        // Hug the title tightly: a loosely hugging stack would grow and squeeze the
        // page area down to the page's minimum instead of letting the page fill it.
        titleBlock.setHuggingPriority(.required, for: .vertical)
        titleBlock.translatesAutoresizingMaskIntoConstraints = false
        pageTitle = label("", size: 28, weight: .medium, color: Palette.ink)
        pageSubtitle = label("", size: 14, color: Palette.body)
        pageSubtitle.maximumNumberOfLines = 2
        pageSubtitle.lineBreakMode = .byWordWrapping
        titleBlock.addArrangedSubview(pageTitle)
        titleBlock.addArrangedSubview(pageSubtitle)
        mainSurface.contentView.addSubview(titleBlock)

        pageScroll = NSScrollView()
        pageScroll.identifier = NSUserInterfaceItemIdentifier("page.scroll")
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

    @objc func navigate(_ sender: NSButton) {
        guard let id = sender.identifier?.rawValue, let page = Page(rawValue: id) else { return }
        show(page: page)
    }

    func show(page: Page) {
        // End editing first: a typed secret is committed before the new page reads
        // it, and no field of the old page is still editing when it is removed.
        window.makeFirstResponder(nil)
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
        pageScroll.hasHorizontalScroller = false
        pageScroll.hasVerticalScroller = true
        // The page is exactly as wide as the visible area and at least as tall: the
        // flexible part of each page (file list, editors, tables) absorbs the rest,
        // so the layout follows the window — full screen included. The page only
        // grows past the visible height, and scrolls, when the window is smaller than
        // the page's minimum.
        let clip = pageScroll.contentView
        let fill = document.heightAnchor.constraint(equalTo: clip.heightAnchor)
        fill.priority = NSLayoutConstraint.Priority(200)
        NSLayoutConstraint.activate([
            document.leadingAnchor.constraint(equalTo: clip.leadingAnchor),
            document.topAnchor.constraint(equalTo: clip.topAnchor),
            document.widthAnchor.constraint(equalTo: clip.widthAnchor),
            document.heightAnchor.constraint(greaterThanOrEqualTo: clip.heightAnchor),
            fill
        ])
        document.needsLayout = true
        document.layoutSubtreeIfNeeded()
        pageScroll.contentView.scroll(to: .zero)
        pageScroll.reflectScrolledClipView(pageScroll.contentView)
        refreshProcessingControls()
    }

    func scrollDocument(for page: Page) -> NSView {
        let document = AutoLayoutDocumentView()
        document.translatesAutoresizingMaskIntoConstraints = false
        let content = vertical([], spacing: 16)
        content.translatesAutoresizingMaskIntoConstraints = false
        document.addSubview(content)
        // Trailing inset matches the header's, so cards end under the language pill.
        NSLayoutConstraint.activate([
            content.leadingAnchor.constraint(equalTo: document.leadingAnchor),
            content.trailingAnchor.constraint(equalTo: document.trailingAnchor, constant: -20),
            content.topAnchor.constraint(equalTo: document.topAnchor),
            content.bottomAnchor.constraint(equalTo: document.bottomAnchor)
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

    func replyWhenJobsFinished() {
        if isTerminating && transcriptionJob == nil && mediaDownloadJob == nil && llmJob == nil && liveJob == nil { NSApp.reply(toApplicationShouldTerminate: true) }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        window?.makeFirstResponder(nil)  // commit a secret still being typed
        isClosing = true
        guard mediaDownloadJob != nil || transcriptionJob != nil || llmJob != nil || liveJob != nil else { return .terminateNow }
        isTerminating = true
        mediaDownloadJob?.cancel()
        transcriptionJob?.cancel()
        transcriptionJob?.terminate()
        llmJob?.terminate()
        liveJob?.terminate()
        return .terminateLater
    }

    @objc func toggleLanguage(_ sender: Any?) {
        let current = defaults.string(forKey: "settings.language") ?? "Русский"
        defaults.set(current == "Русский" ? "English" : "Русский", forKey: "settings.language")
        rebuildInterface()
    }

    @objc func toggleDarkTheme(_ sender: Any?) {
        defaults.set(Palette.isDark ? "Светлая" : "Тёмная", forKey: "settings.theme")
        rebuildInterface()
    }

    /// `activate: false` for a rebuild the user did not ask for (the system
    /// appearance changed): it must not pull the window to the front.
    func rebuildInterface(activate: Bool = true) {
        window.makeFirstResponder(nil)
        let page = currentPage
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.installMainMenu()
            self.buildWindowContent()
            self.show(page: page)
            if activate { self.window.makeKeyAndOrderFront(nil) }
        }
    }

    @objc func toggleSearch(_ sender: NSButton) {
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

    /// Shows the pages whose title matches the search field's text.
    func updateSearchResults(for field: NSSearchField) {
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

    @objc func selectSearchResult(_ sender: NSButton) {
        searchResults.removeFromSuperview()
        navigate(sender)
    }

    func showNotice(_ title: String, _ message: String) {
        let alert = NSAlert()
        alert.messageText = L10n.text(title)
        alert.informativeText = L10n.text(message)
        alert.addButton(withTitle: L10n.text("Понятно"))
        alert.beginSheetModal(for: window)
    }
}

// Each delegate method lives in the extension that declares its conformance, so
// the Objective-C runtime sees it as the protocol's witness.
extension AppController: NSWindowDelegate {
    func windowWillClose(_ notification: Notification) {
        window.makeFirstResponder(nil)  // commit a secret still being typed
        isClosing = true
        mediaImportAlert = nil
        mediaDownloadJob?.cancel()
        transcriptionJob?.cancel()
        transcriptionJob?.terminate()
        llmJob?.terminate()
        liveJob?.terminate()
        llmToolsQuery?.cancel()
        llmToolChecks.values.forEach { $0.cancel() }
        pendingToolChecks.values.forEach { $0.cancel() }
        cleanupDownloadedMedia()
    }
}
