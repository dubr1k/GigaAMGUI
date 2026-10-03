import AppKit
import GigaAMLiquidCore
import UniformTypeIdentifiers

/// The application delegate and the window's controller. Its state lives here;
/// each page, the shared control factory, media import and persistence are
/// extensions in their own files (Pages/, UI/, MediaImportFlow.swift,
/// Persistence.swift). Members used across those files are internal.
final class AppController: NSObject, NSApplicationDelegate, NSWindowDelegate, NSSearchFieldDelegate, NSTextViewDelegate {
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
    var transcriptionLog = ""
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
        window.appearance = NSAppearance(named: Palette.isDark ? .darkAqua : .aqua)
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

    func buildAPI(into content: NSStackView) {
        let examples = card("Примеры запросов")
        let body = contentStack(examples)
        body.spacing = 10
        body.addArrangedSubview(label("Статус API", size: 12, color: Palette.muted))
        body.addArrangedSubview(label("○  Не подключён", size: 14, weight: .medium, color: Palette.body))
        body.addArrangedSubview(label("http://127.0.0.1:8000  •  адрес примера", size: 14, color: Palette.body))
        body.addArrangedSubview(horizontal([
            button("Скопировать URL", action: #selector(copyAPIURL(_:))),
            button("Открыть документацию", primary: true, action: #selector(openDocumentation(_:))),
            flexibleSpace()
        ], spacing: 12))
        let language = PillSelector(labels: ["Python", "cURL", "JavaScript"], target: self, action: #selector(apiLanguageChanged(_:)))
        language.selectedSegment = ["Python", "cURL", "JavaScript"].firstIndex(of: defaults.string(forKey: "api.exampleLanguage") ?? "Python") ?? 0
        language.heightAnchor.constraint(equalToConstant: 32).isActive = true
        body.addArrangedSubview(language)
        let code = codeView(apiExample(language.selectedSegment))
        apiCodeText = code.documentView as? NSTextView
        code.heightAnchor.constraint(greaterThanOrEqualToConstant: 180).isActive = true
        stretchy(code)
        body.addArrangedSubview(code)
        body.addArrangedSubview(horizontal([button("Копировать пример", action: #selector(copyCode(_:))), flexibleSpace()], spacing: 12))
        body.addArrangedSubview(wrappedLabel("Пример запроса, не ответ сервера. Этот клиент не запускает API.", size: 12, color: Palette.muted))
        body.bottomAnchor.constraint(equalTo: examples.bottomAnchor, constant: -16).isActive = true
        let docs = card("Документация")
        for title in ["Быстрый старт", "Эндпоинты", "Параметры", "Примеры", "Форматы ответов", "Скачать OpenAPI (JSON)"] {
            let row = documentationRow(title)
            if title == "Скачать OpenAPI (JSON)" {
                row.isEnabled = false
                row.toolTip = L10n.text("Схема доступна после подключения API.")
            }
            contentStack(docs).addArrangedSubview(row)
        }
        docs.widthAnchor.constraint(equalToConstant: 292).isActive = true
        content.addArrangedSubview(stretchy(fillRow([examples, docs], spacing: 16)))
    }

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

    func buildSettings(into content: NSStackView) {
        settingsCategoryButtons.removeAll()
        let names = ["Общие", "Модели", "Обработка", "Диаризация", "Аудио", "LLM", "API", "Пути", "О приложении"]
        let stored = defaults.string(forKey: "settings.category") ?? "Общие"
        let selected = names.contains(stored) ? stored : "Общие"
        let categories = GlassView(radius: 18)
        let rail = vertical([], spacing: 8)
        for name in names {
            rail.addArrangedSubview(settingsCategoryButton(name, selected: name == selected))
        }
        embed(rail, in: categories.contentView, inset: 18, top: 30)
        categories.widthAnchor.constraint(equalToConstant: 260).isActive = true
        let detail = NSView()
        settingsDetail = detail
        content.addArrangedSubview(stretchy(fillRow([categories, detail], spacing: 16)))
        applySettingsCategorySelection(selected)
    }

    func settingsPage(_ category: String) -> NSView {
        let surface = GlassView(radius: 28)
        let body = vertical([label(category, size: 24, weight: .medium, color: Palette.ink)], spacing: 24)
        switch category {
        case "Общие":
            body.addArrangedSubview(wrappedLabel("Язык и оформление приложения. Изменения сохраняются автоматически.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Язык интерфейса", control: popup(["Русский", "English"], key: "settings.language")))
            body.addArrangedSubview(settingsField("Тема", control: popup(["Системная", "Светлая", "Тёмная"], key: "settings.theme")))
        case "Модели":
            body.addArrangedSubview(wrappedLabel("Модель распознавания и устройство вычислений.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.spacing = 16
            body.addArrangedSubview(settingsField("ASR backend", control: popup(["auto", "mlx", "onnx", "pytorch"], key: "settings.backend")))
            body.addArrangedSubview(settingsField("Модель", control: popup(["v3_e2e_rnnt", "multilingual_ctc", "multilingual_large_ctc"], key: "settings.model")))
            body.addArrangedSubview(settingsField("ONNX provider", control: popup(["auto", "cpu", "cuda", "tensorrt", "coreml", "directml"], key: "settings.onnxProvider")))
            body.addArrangedSubview(settingsField("Устройство", control: inactive(popup(["Auto / GPU", "CPU", "GPU"], key: "settings.device"))))
            body.addArrangedSubview(inactive(toggleRow("Fallback на CPU", key: "settings.cpuFallback", defaultValue: false)))
            body.addArrangedSubview(wrappedLabel("Устройство и fallback выбирает backend. ONNX provider применяется только к ONNX. Язык определяется моделью.", size: 12, color: Palette.muted))
        case "Обработка":
            body.addArrangedSubview(wrappedLabel("Разбиение транскрипции и оформление субтитров.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(toggleRow("Разбивать по предложениям", key: "subtitle.sentences", defaultValue: true))
            body.addArrangedSubview(equalColumns([
                settingsField("Строк в блоке", control: popup(["2", "1", "3", "4"], key: "subtitle.lines")),
                settingsField("Символов в строке", control: popup(["64", "42", "80"], key: "subtitle.characters"))
            ], spacing: 20))
            body.addArrangedSubview(inactive(toggleRow("Показывать спикера", key: "settings.showSpeaker", defaultValue: true)))
            body.addArrangedSubview(wrappedLabel("Метки спикеров добавляются автоматически при включённой диаризации.", size: 12, color: Palette.muted))
        case "Диаризация":
            body.addArrangedSubview(wrappedLabel("Разделение речи по спикерам и доступ к моделям Hugging Face.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(toggleRow("Диаризация", key: "settings.diarization", defaultValue: false))
            body.addArrangedSubview(settingsField("Движок диаризации", control: popup(["pyannote", "onnx", "sortformer"], key: "settings.diarizationEngine")))
            body.addArrangedSubview(settingsField("Кол-во спикеров", control: speakerCountPopup()))
            body.addArrangedSubview(wrappedLabel("Sortformer определяет спикеров автоматически (до 4). Pyannote и ONNX принимают известное число спикеров.", size: 12, color: Palette.muted))
            let value = SecureStore.string(for: "hfToken") ?? ""
            let token = RoundedSecureTextField(string: value)
            token.cell = CenteredSecureTextCell(textCell: value)
            token.isEditable = true
            token.isSelectable = true
            token.isBezeled = false
            token.drawsBackground = false
            token.wantsLayer = true
            token.layer?.cornerRadius = 18
            token.layer?.masksToBounds = true
            token.placeholderString = L10n.text("Не настроен")
            token.identifier = NSUserInterfaceItemIdentifier("settings.hfToken")
            token.target = self
            token.delegate = self
            token.action = #selector(textChanged(_:))
            body.addArrangedSubview(settingsField("HF Token", control: token))
            body.addArrangedSubview(wrappedLabel("Pyannote требует токен и принятые лицензии моделей Hugging Face. Пустое поле использует HF_TOKEN из окружения.", size: 12, color: Palette.muted))
        case "Аудио":
            body.addArrangedSubview(wrappedLabel("Параметры аудиосигнала для обработки.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Подготовка аудио", control: popup(["auto", "off", "light", "denoise"], key: "processing.preprocessing")))
            body.addArrangedSubview(settingsField("Частота дискретизации", control: inactive(popup(["16000 Hz"], key: "settings.sampleRate"))))
            body.addArrangedSubview(wrappedLabel("Частоту 16000 Hz задаёт конвертер. auto — автоматическая подготовка, off — без неё, light — лёгкая обработка, denoise — шумоподавление.", size: 12, color: Palette.muted))
        case "LLM":
            body.addArrangedSubview(wrappedLabel("Провайдер постобработки и вопросов ассистенту в Live. API — OpenAI-совместимый или Anthropic адрес; остальные — локальные CLI.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.spacing = 16
            body.addArrangedSubview(settingsField("Провайдер", control: popup(Self.llmProviders, key: "llm.provider")))
            body.addArrangedSubview(settingsField("API URL", control: editableText(defaults.string(forKey: "llm.apiUrl") ?? "", key: "llm.apiUrl", placeholder: "https://api.openai.com/v1")))
            body.addArrangedSubview(settingsField("API Key", control: secureField(account: "llmApiKey", key: "llm.apiKey")))
            body.addArrangedSubview(equalColumns([
                settingsField("Модель", control: editableText(defaults.string(forKey: "llm.model") ?? "", key: "llm.model", placeholder: "gpt-4.1-mini")),
                settingsField("Temperature", control: editableText(defaults.string(forKey: "llm.temperature") ?? "", key: "llm.temperature", placeholder: "0.2"))
            ], spacing: 20))
            body.addArrangedSubview(divider())
            let rescan = button("Пересканировать", action: #selector(rescanLLMTools(_:)), height: 30)
            rescan.setAccessibilityIdentifier("llm.rescan")
            llmRescanButton = rescan
            body.addArrangedSubview(horizontal([label("Инструменты", size: 17, weight: .medium, color: Palette.ink), flexibleSpace(), rescan], spacing: 12))
            body.addArrangedSubview(wrappedLabel("Пустой путь — автопоиск по PATH и типичным каталогам (homebrew, npm, bun, nvm). Приложение из Finder не видит PATH терминала — поиск ведёт Python-сервис.", size: 12, color: Palette.muted))
            llmToolRows = [:]
            for tool in Self.llmCliProviders {
                body.addArrangedSubview(llmToolRow(tool))
            }
            body.addArrangedSubview(divider())
            body.addArrangedSubview(label("Дополнительные параметры", size: 17, weight: .medium, color: Palette.ink))
            body.addArrangedSubview(wrappedLabel("Обычно не нужны — всё работает с пустыми полями. «Аргументы» — флаги командной строки, которые добавляются к запуску CLI как есть (как в терминале): например, уровень рассуждений или профиль. «Provider» у Pi и oh-my-pi — внутренний поставщик модели (anthropic, openai, google…), если нужно переопределить настроенный в самом CLI. Модель берётся из общего поля «Модель» выше.", size: 12, color: Palette.muted))
            for tool in Self.llmCliProviders {
                let argsField = editableText(defaults.string(forKey: "llm.\(tool.prefix)Args") ?? "", key: "llm.\(tool.prefix)Args", placeholder: Self.llmArgsExamples[tool.prefix] ?? "")
                argsField.toolTip = L10n.text("Флаги командной строки, добавляются к запуску как есть. Пример: ") + (Self.llmArgsExamples[tool.prefix] ?? "")
                var fields: [NSView] = [settingsField("\(tool.name) — аргументы", control: argsField)]
                if tool.hasProvider {
                    let providerField = editableText(defaults.string(forKey: "llm.\(tool.prefix)Provider") ?? "", key: "llm.\(tool.prefix)Provider", placeholder: "по умолчанию из конфига CLI")
                    providerField.toolTip = L10n.text("Внутренний поставщик модели: anthropic, openai, google, openrouter… Пусто — как настроено в ") + tool.name
                    fields.append(settingsField("\(tool.name) — поставщик модели", control: providerField))
                }
                body.addArrangedSubview(fields.count == 1 ? fields[0] : equalColumns(fields, spacing: 20))
            }
            body.addArrangedSubview(divider())
            body.addArrangedSubview(wrappedLabel("«Другое» — любая своя команда. Промпт передаётся последним аргументом и одновременно в stdin; напишите {stdin} в аргументах, чтобы передавать только через stdin. Ответ читается из stdout.", size: 12, color: Palette.muted))
            body.addArrangedSubview(equalColumns([
                settingsField("Другое — команда", control: editableText(defaults.string(forKey: "llm.otherPath") ?? "", key: "llm.otherPath", placeholder: "/usr/local/bin/my-llm")),
                settingsField("Другое — аргументы", control: editableText(defaults.string(forKey: "llm.otherArgs") ?? "", key: "llm.otherArgs", placeholder: "--model x {stdin}"))
            ], spacing: 20))
            let allowTools = toggleRow("Разрешить инструменты и сессии агента", key: "llm.allowTools", defaultValue: false)
            allowTools.toolTip = L10n.text("Выключено: claude/codex/opencode/pi/omp запускаются как чистый запрос к модели — без доступа к файлам и без записи в историю сессий агента.")
            body.addArrangedSubview(allowTools)
            body.addArrangedSubview(wrappedLabel("Выключено — CLI работает как чистый запрос к модели: агент не читает файлы и не сохраняет сессию. Включайте, только если хотите, чтобы он мог пользоваться своими инструментами. Ключ API хранится в Связке ключей.", size: 12, color: Palette.muted))
            refreshLLMToolRows()
            refreshLLMTools(fresh: false)
        case "API":
            body.addArrangedSubview(wrappedLabel("Отдельный REST API запускается из api.py. Нативный клиент не запускает сервер и не проверяет его доступность.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(label("Не подключён", size: 17, weight: .medium, color: Palette.body))
            body.addArrangedSubview(settingsField("Адрес в примерах · не настройка подключения", control: label("http://127.0.0.1:8000", size: 15, color: Palette.ink)))
            body.addArrangedSubview(horizontal([button("Скопировать URL", action: #selector(copyAPIURL(_:))), button("Открыть документацию", action: #selector(openDocumentation(_:)))], spacing: 12))
            body.addArrangedSubview(wrappedLabel("Для запросов требуется заголовок Authorization: Bearer <ключ> (X-API-Key принимается как устаревший вариант). Примеры Python, cURL и JavaScript доступны в разделе API основного меню.", size: 13, color: Palette.body))
        case "Пути":
            body.addArrangedSubview(wrappedLabel("Хранение результатов и визуальные эффекты приложения.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Папка результатов", control: editableText(outputPathText, key: "output.path", placeholder: "Рядом с исходным файлом")))
            body.addArrangedSubview(toggleRow("Liquid Glass", key: "settings.liquidGlass", defaultValue: true))
        case "О приложении":
            body.spacing = 18
            let releaseVersion = (Bundle.main.object(forInfoDictionaryKey: "GigaAMReleaseVersion") as? String)
                ?? (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
                ?? "—"
            body.addArrangedSubview(label("GigaAM v3 Transcriber", size: 22, weight: .medium, color: Palette.ink))
            body.addArrangedSubview(wrappedLabel("Транскрибация русской речи из аудио и видео на базе GigaAM-v3.", size: 14, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Версия приложения", control: label(releaseVersion, size: 15, color: Palette.ink)))
            body.addArrangedSubview(wrappedLabel("Импорт медиа, распознавание, live-захват и LLM используют встроенные Python-сервисы проекта; звук захватывает само приложение.", size: 13, color: Palette.body))
            body.addArrangedSubview(label("Разработчики приложения", size: 12, color: Palette.muted))
            let developers = ["dubr1k", "Baggrisha"].map { name in
                let link = button(name, action: #selector(openDeveloper(_:)))
                link.identifier = NSUserInterfaceItemIdentifier(name)
                link.toolTip = "https://github.com/\(name)"
                return link
            }
            body.addArrangedSubview(horizontal(developers + [flexibleSpace()], spacing: 12))
            body.addArrangedSubview(settingsField("Модель распознавания", control: label("SaluteDevices / GigaAM", size: 15, color: Palette.ink)))
            body.addArrangedSubview(centered(button("Проект на GitHub", action: #selector(openProject(_:)))))
        default: break
        }
        // The panel is as tall as the window allows; a body taller than that (the LLM
        // tools table, or any page in a short window) scrolls inside the panel
        // instead of stretching the page. 24 + 6 pt body inset = 30 pt.
        embed(scrollable(body), in: surface.contentView, inset: 24, fillHeight: true)
        return surface
    }

    func apiExample(_ language: Int) -> String {
        switch language {
        case 1:
            return """
            # Пример: задайте GIGAAM_API_KEY и путь к файлу
            curl http://127.0.0.1:8000/v1/audio/transcriptions \\
              -H "Authorization: Bearer $GIGAAM_API_KEY" \\
              -F file=@meeting.mp3 -F model=whisper-1 -F response_format=verbose_json
            """
        case 2:
            return """
            // Пример для Node.js; задайте GIGAAM_API_KEY
            import OpenAI from "openai";
            import fs from "node:fs";

            const client = new OpenAI({
              baseURL: "http://127.0.0.1:8000/v1",
              apiKey: process.env.GIGAAM_API_KEY,
            });
            const result = await client.audio.transcriptions.create({
              model: "whisper-1",
              file: fs.createReadStream("meeting.mp3"),
              response_format: "verbose_json",
            });
            console.log(result.text);
            """
        default:
            return """
            # Пример: задайте GIGAAM_API_KEY и путь к файлу
            import os
            from openai import OpenAI

            client = OpenAI(
                base_url="http://127.0.0.1:8000/v1",
                api_key=os.environ["GIGAAM_API_KEY"],
            )
            with open("meeting.mp3", "rb") as audio:
                result = client.audio.transcriptions.create(
                    model="whisper-1", file=audio,
                    response_format="verbose_json")
            print(result.text)
            """
        }
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

    func documentationRow(_ title: String) -> NSButton {
        let button = button(title, action: #selector(openDocumentation(_:)), height: 46)
        button.identifier = NSUserInterfaceItemIdentifier(title)
        button.alignment = .left
        button.image = NSImage(systemSymbolName: "chevron.right", accessibilityDescription: nil)
        button.imagePosition = .imageTrailing
        button.font = NSFont.systemFont(ofSize: 14)
        return button
    }

    func settingsCategoryButton(_ title: String, selected: Bool) -> NSButton {
        let button = NavigationRowButton(title: L10n.text(title), target: self, action: #selector(selectSettingsCategory(_:)))
        button.identifier = NSUserInterfaceItemIdentifier("settings.category.\(title)")
        button.isBordered = false
        button.bezelStyle = .regularSquare
        button.alignment = .left
        button.font = NSFont.systemFont(ofSize: 14, weight: .medium)
        button.wantsLayer = true
        button.layer?.cornerRadius = 9
        button.widthAnchor.constraint(equalToConstant: 224).isActive = true
        button.heightAnchor.constraint(equalToConstant: 38).isActive = true
        settingsCategoryButtons[title] = button
        button.contentTintColor = selected ? Palette.ink : Palette.body
        button.layer?.backgroundColor = (selected ? Palette.selection : .clear).cgColor
        return button
    }

    func applySettingsCategorySelection(_ selectedTitle: String) {
        guard let settingsDetail, settingsCategoryButtons[selectedTitle] != nil else { return }
        window.makeFirstResponder(nil)
        defaults.set(selectedTitle, forKey: "settings.category")
        for (title, button) in settingsCategoryButtons {
            let isSelected = title == selectedTitle
            button.contentTintColor = isSelected ? Palette.blue : Palette.body
            button.layer?.backgroundColor = (isSelected ? Palette.selection : .clear).cgColor
        }
        settingsDetail.subviews.forEach { $0.removeFromSuperview() }
        embed(settingsPage(selectedTitle), in: settingsDetail, inset: 0, fillHeight: true)
    }

    func option(_ key: String, values: [String]) -> String {
        let stored = defaults.string(forKey: key) ?? values[0]
        return values.contains(stored) ? stored : values[0]
    }

    func enabledOption(_ key: String, defaultValue: Bool) -> Bool {
        defaults.object(forKey: key) == nil ? defaultValue : defaults.bool(forKey: key)
    }

    func replyWhenJobsFinished() {
        if isTerminating && transcriptionJob == nil && mediaDownloadJob == nil && llmJob == nil && liveJob == nil { NSApp.reply(toApplicationShouldTerminate: true) }
    }

    @objc func popupChanged(_ sender: NSPopUpButton) {
        guard let key = sender.identifier?.rawValue, let value = sender.titleOfSelectedItem else { return }
        if key == "live.microphone" {
            // Titles are device names; persist the stable device id instead.
            defaults.set(liveDeviceIDs.indices.contains(sender.indexOfSelectedItem) ? liveDeviceIDs[sender.indexOfSelectedItem] : "default", forKey: key)
            return
        }
        defaults.set(value, forKey: key)
        if key == "settings.theme" || key == "settings.language" { rebuildInterface() }
        if key == "settings.diarizationEngine" { resetManualSpeakerCountIfUnavailable() }
        if key == "llm.provider" { refreshLLMProviderStatus() }
        refreshProcessingControls()
    }

    @objc func switchChanged(_ sender: NSButton) {
        guard let key = sender.identifier?.rawValue else { return }
        defaults.set(sender.state == .on, forKey: key)
        if key == "settings.liquidGlass" { rebuildInterface() }
        if key == "settings.diarization" { resetManualSpeakerCountIfUnavailable() }
        refreshProcessingControls()
    }

    @objc func textChanged(_ sender: NSTextField) {
        guard let key = sender.identifier?.rawValue else { return }
        if key == "settings.hfToken" {
            do {
                try SecureStore.set(sender.stringValue.trimmingCharacters(in: .whitespacesAndNewlines), for: "hfToken")
            } catch {
                showNotice("Не удалось сохранить HF Token", error.localizedDescription)
            }
        } else if key == "llm.apiKey" {
            do {
                try SecureStore.set(sender.stringValue.trimmingCharacters(in: .whitespacesAndNewlines), for: "llmApiKey")
            } catch {
                showNotice("Не удалось сохранить API Key", error.localizedDescription)
            }
        } else {
            defaults.set(sender.stringValue, forKey: key)
        }
        if key == "output.path" { refreshProcessingControls() }
        if key == "live.sessionRoot", liveJob == nil {
            liveSessionDir = nil
            refreshLiveFolderLabel()
        }
        // An edited CLI path invalidates its badge: re-probe just that tool.
        if key.hasPrefix("llm."), key.hasSuffix("Path"),
           let tool = Self.llmCliProviders.first(where: { "llm.\($0.prefix)Path" == key }) {
            checkLLMTool(tool.name)
        }
    }

    @objc func chooseMediaURL(_ sender: Any?) {
        guard !isClosing, transcriptionJob == nil, mediaDownloadJob == nil, window.attachedSheet == nil else { return }
        presentMediaURLSheet()
    }

    func presentMediaURLSheet(value: String = "", error: String? = nil) {
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

    func downloadMedia(_ url: URL) {
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

    func windowWillClose(_ notification: Notification) {
        isClosing = true
        mediaImportAlert = nil
        mediaDownloadJob?.cancel()
        transcriptionJob?.cancel()
        transcriptionJob?.terminate()
        llmJob?.terminate()
        liveJob?.terminate()
        llmToolsQuery?.cancel()
        llmToolChecks.values.forEach { $0.cancel() }
        cleanupDownloadedMedia()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
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

    func mediaCacheRoot() -> URL? {
        try? FileManager.default.url(
            for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        ).appendingPathComponent("GigaAMLiquid/Media", isDirectory: true).standardizedFileURL
    }

    func rememberDownloadedMedia(_ files: [URL]) {
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

    // MARK: - LLM

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

    // MARK: - LLM CLI tools (registry lives in the Python worker)

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

    @objc func openDocumentation(_ sender: NSButton) {
        let title = sender.identifier?.rawValue ?? "Открыть документацию"
        let detail: String
        switch title {
        case "Параметры":
            detail = "POST /v1/audio/transcriptions принимает multipart-поле file. Основные поля: model (whisper-1 и другие алиасы), response_format (json/text/srt/vtt/verbose_json/diarized_json), stream, timestamp_granularities[]. Расширения GigaAM: diarize, diarization_backend, num_speakers, asr_backend, onnx_provider, audio_preprocessing. Требуется заголовок Authorization: Bearer <ключ>."
        case "Форматы ответов":
            detail = "Ответ возвращается синхронно, сразу в запросе. Формат задаётся response_format: json, text, srt, vtt, verbose_json или diarized_json. При stream=true json/verbose_json приходят по SSE: во время обработки — комментарии прогресса, дельты текста — после распознавания всего файла."
        case "Эндпоинты":
            detail = "POST /v1/audio/transcriptions — распознать файл.\nGET /v1/models — доступные модели.\nGET /health — состояние сервиса."
        case "Примеры":
            detail = "Выберите Python, cURL или JavaScript слева. Кнопка «Копировать пример» копирует показанный код. Задайте свой API-ключ и путь к аудиофайлу."
        default:
            detail = "Отдельный REST API запускается командой python api.py. REST API совместим с OpenAI Audio API: укажите base_url http://127.0.0.1:8000/v1 в любом клиенте OpenAI. Примеры на этой странице соответствуют POST /v1/audio/transcriptions.\n\nЭтот нативный клиент не запускает сервер и не проверял его доступность."
        }
        showNotice(title, detail)
    }

    @objc func openProject(_ sender: Any?) {
        guard let url = URL(string: "https://github.com/dubr1k/GigaAMGUI") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc func openDeveloper(_ sender: NSButton) {
        guard let name = sender.identifier?.rawValue,
              ["Baggrisha", "dubr1k"].contains(name),
              let url = URL(string: "https://github.com/\(name)") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc func copyAPIURL(_ sender: Any?) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("http://127.0.0.1:8000", forType: .string)
    }

    @objc func copyCode(_ sender: Any?) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(apiCodeText?.string ?? "", forType: .string)
    }

    @objc func apiLanguageChanged(_ sender: PillSelector) {
        let names = ["Python", "cURL", "JavaScript"]
        defaults.set(names[sender.selectedSegment], forKey: "api.exampleLanguage")
        apiCodeText?.string = apiExample(sender.selectedSegment)
    }

    func textDidChange(_ notification: Notification) {
        guard let editor = notification.object as? NSTextView, let key = editor.identifier?.rawValue else { return }
        defaults.set(editor.string, forKey: key)
    }

    @objc func selectSettingsCategory(_ sender: NSButton) {
        guard let rawValue = sender.identifier?.rawValue,
              rawValue.hasPrefix("settings.category.") else { return }
        applySettingsCategorySelection(String(rawValue.dropFirst("settings.category.".count)))
        refreshProcessingControls()
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

    func rebuildInterface() {
        window.makeFirstResponder(nil)
        let page = currentPage
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.installMainMenu()
            self.buildWindowContent()
            self.show(page: page)
            self.window.makeKeyAndOrderFront(nil)
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

    /// No controlTextDidEndEditing: every edit is already persisted here. Saving
    /// again on end-editing let a stale field win — show(page:) removes the old,
    /// still-focused field only after the new page has read the stored value, and
    /// AppKit ends its editing on removal, so a folder picked with «Изменить» was
    /// overwritten by the old empty text while the new field still displayed it.
    func controlTextDidChange(_ notification: Notification) {
        guard let control = notification.object as? NSTextField else { return }
        if control.identifier != nil { textChanged(control) }
        guard let field = control as? NSSearchField, field === searchField else { return }
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
