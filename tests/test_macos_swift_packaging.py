from pathlib import Path

WORKFLOW = Path(".github/workflows/build.yml")


def test_swift_release_job_builds_and_archives_native_app() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "build-macos-swift:" in text
    assert "swift build -c release --package-path macos/GigaAMLiquid" in text
    assert "CFBundleShortVersionString" in text
    assert "codesign --verify --deep --strict" in text
    assert 'lipo -archs "$APP/Contents/MacOS/GigaAMLiquid"' in text
    assert "GigaAMLiquid-macos-arm64-${SAFE_REF_NAME}" in text
    assert "GigaAMLiquid-macos-arm64-offline-${SAFE_REF_NAME}" in text
    assert "--native-worker" in text
    assert "git archive HEAD" not in text
    assert "pattern: GigaAMTranscriber-macos-app-v*" in text
    assert 'test ! -e "$RESOURCES/app.py"' in text
    assert 'test ! -e "$RESOURCES/src"' in text


def test_swift_release_archives_contain_a_single_self_contained_app() -> None:
    # С 2.1.1 в архиве одно приложение: companion и (офлайн) модели лежат внутри
    # GigaAMLiquid.app/Contents/Resources, а не рядом с ним.
    text = WORKFLOW.read_text(encoding="utf-8")
    online = text.split("Assemble and verify app bundle", 1)[1].split("Upload native Swift artifact", 1)[0]
    assert 'RESOURCES="$APP/Contents/Resources"' in online
    assert 'unzip -q "$COMPANION_ARCHIVE" -d "$RESOURCES"' in online
    assert 'COMPANION="$RESOURCES/GigaAMTranscriber.app/Contents/MacOS/GigaAMTranscriber"' in online
    assert 'test ! -e "stage/$PRODUCT/GigaAMTranscriber.app"' in online
    # Вложенный companion подписывается вместе с внешним бандлом и только после того, как положен внутрь.
    assert online.index('unzip -q "$COMPANION_ARCHIVE"') < online.index('codesign --force --deep --sign - "$APP"')
    offline = text.split("Assemble and verify offline native Swift archive", 1)[1].split("Upload offline native Swift artifact", 1)[0]
    assert 'RESOURCES="$SWIFT_APP/Contents/Resources"' in offline
    assert 'unzip -q "$COMPANION_ARCHIVE" -d "$RESOURCES"' in offline
    assert 'test -d "$RESOURCES/models/hf"' in offline
    assert 'test ! -e "$ROOT/GigaAMTranscriber.app"' in offline and 'test ! -e "$ROOT/models"' in offline
    assert 'codesign --force --deep --sign - "$SWIFT_APP"' in offline
    assert offline.index('unzip -q "$COMPANION_ARCHIVE"') < offline.index('codesign --force --deep --sign - "$SWIFT_APP"')
    assert offline.index('codesign --force --deep --sign - "$SWIFT_APP"') < offline.index("native_worker_smoke.py")


def test_offline_swift_archive_runs_a_real_file_through_the_companion() -> None:
    # pong не ловит ни колесо scipy, которое dyld не грузит, ни auto-backend,
    # уходящий офлайн за MLX-моделью: гейт гоняет файл с пустым кэшем и
    # HF_HUB_OFFLINE=1 — ровно так companion запускает GigaAMLiquid.
    text = WORKFLOW.read_text(encoding="utf-8")
    offline = text.split("Assemble and verify offline native Swift archive", 1)[1].split("Upload offline native Swift artifact", 1)[0]
    assert "python3 scripts/native_worker_smoke.py" in offline
    assert 'HF_HOME="${RUNNER_TEMP}/liquid-offline-smoke-hf" HF_HUB_OFFLINE=1' in offline
    assert "--backend" not in offline  # auto, как у пользователя по умолчанию


def test_offline_swift_archive_runs_live_smoke_through_the_companion() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    offline = text.split("Assemble and verify offline native Swift archive", 1)[1].split("Upload offline native Swift artifact", 1)[0]
    assert "python3 scripts/native_worker_smoke.py --live" in offline


def test_offline_smoke_uses_a_committed_clip_instead_of_say() -> None:
    # v2.1.1: `say` на раннере выдал почти пустой клип, батч-smoke пропустил пустой
    # текст, а live-гейт честно упал без единого final. Клип теперь фикстура в
    # репозитории, и оба smoke требуют непустой транскрипт.
    text = WORKFLOW.read_text(encoding="utf-8")
    offline = text.split("Assemble and verify offline native Swift archive", 1)[1].split("Upload offline native Swift artifact", 1)[0]
    assert "say -o" not in offline
    assert 'SMOKE_CLIP="tests/fixtures/liquid_smoke.wav"' in offline
    clip = Path("tests/fixtures/liquid_smoke.wav")
    assert clip.is_file() and 40_000 < clip.stat().st_size < 400_000
    source = Path("scripts/native_worker_smoke.py").read_text(encoding="utf-8")
    assert "transcript is empty" in source


def test_native_worker_smoke_has_live_mode() -> None:
    source = Path("scripts/native_worker_smoke.py").read_text(encoding="utf-8")
    assert "def run_live_smoke(" in source
    assert '"type": "live_start"' in source and '"type": "live_audio"' in source and '"type": "live_stop"' in source
    assert '"--live"' in source


def test_release_waits_for_and_downloads_swift_artifact() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "needs: [build, build-macos-full, build-macos-intel, build-macos-swift]" in text
    assert "pattern: GigaAM*" in text


def test_swift_client_documents_bundled_companion_runtime() -> None:
    text = Path("macos/GigaAMLiquid/README.md").read_text(encoding="utf-8")
    assert "GigaAMTranscriber.app" in text
    assert "do not require a separately installed Python environment" in text
    assert "downloads model files when they are first needed" in text


def test_hugging_face_token_uses_keychain_instead_of_user_defaults() -> None:
    main = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift").read_text(encoding="utf-8")
    secure_store = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/SecureStore.swift").read_text(encoding="utf-8")
    assert 'SecureStore.string(for: "hfToken")' in main
    assert 'SecureStore.set(sender.stringValue' in main
    assert 'defaults.set(sender.stringValue, forKey: key)' in main
    assert "kSecClassGenericPassword" in secure_store


def test_native_client_blocks_colliding_output_stems_and_cleans_download_cache() -> None:
    main = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift").read_text(encoding="utf-8")
    assert "let groupedStems = Dictionary(grouping: selectedFileURLs)" in main
    assert "перезапишут результаты друг друга" in main
    assert "rememberDownloadedMedia(files)" in main
    assert "cleanupDownloadedMedia()" in main


def test_swift_runtime_prefers_frozen_offline_companion() -> None:
    runtime = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/PythonRuntime.swift").read_text(encoding="utf-8")
    assert "GigaAMTranscriber.app/Contents/MacOS/GigaAMTranscriber" in runtime
    assert 'frozenCompanion ? ["--native-worker"]' in runtime
    assert "hasSourceRuntime || manager.isExecutableFile" in runtime
    assert "companion != nil && hasBundledModels(root: root)" in runtime
    assert 'childEnvironment["HF_HUB_OFFLINE"] = "1"' in runtime


def test_swift_runtime_prefers_companion_embedded_in_its_own_bundle() -> None:
    # Самостоятельный .app: сначала Contents/Resources собственного бандла, потом
    # старая раскладка «рядом» и исходники. Worker не должен писать в подписанный
    # бандл (processing_stats.json идёт в cwd), поэтому cwd — Application Support.
    runtime = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/PythonRuntime.swift").read_text(encoding="utf-8")
    assert "let workingDirectory: URL" in runtime
    resolve = runtime.split("private static func projectRoot(", 1)[1].split("private static func pythonURL(", 1)[0]
    assert "Bundle.main.resourceURL" in resolve
    assert resolve.index("Bundle.main.resourceURL") < resolve.index("Bundle.main.executableURL?.deletingLastPathComponent()")
    assert "applicationSupportDirectory" in runtime
    for name in ("WorkerProcess.swift", "MediaImport.swift"):
        source = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid", name).read_text(encoding="utf-8")
        assert "currentDirectoryURL = runtime.workingDirectory" in source, name
        assert "currentDirectoryURL = runtime.root" not in source and "currentDirectoryURL = root" not in source, name


def test_swift_job_launch_does_not_require_source_tree_with_frozen_companion() -> None:
    # Release archives ship GigaAMLiquid.app + GigaAMTranscriber.app and no src/;
    # the legacy `python -m src.tui_worker` guard must not run in companion mode.
    transcription = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/Transcription.swift").read_text(encoding="utf-8")
    launch = transcription.split("private func launch() throws {", 1)[1].split("let task = Process()", 1)[0]
    assert "let runtime = try PythonRuntime.resolve()" in launch
    assert 'guard runtime.frozenCompanion || manager.isReadableFile(atPath: runtime.root.appendingPathComponent("src/tui_worker.py").path)' in launch


def test_swift_client_installs_main_menu_with_standard_shortcuts() -> None:
    # SwiftPM-исполняемый файл не несёт MainMenu.nib: без программного меню в
    # menu bar только имя приложения, а ⌘Q/⌘W/⌘C/⌘V не имеют key equivalents.
    main = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift").read_text(encoding="utf-8")
    menu = main.split("private func installMainMenu()", 1)[1].split("\n    }\n", 1)[0]
    assert "NSApp.mainMenu = " in menu
    assert "#selector(NSApplication.terminate(_:))" in menu and 'keyEquivalent: "q"' in menu
    assert "#selector(NSWindow.performClose(_:))" in menu and 'keyEquivalent: "w"' in menu
    assert "#selector(NSText.paste(_:))" in menu and 'keyEquivalent: "v"' in menu
    assert "NSApp.windowsMenu = " in menu
    assert "func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }" in main
    assert "installMainMenu()" in main.split("func applicationDidFinishLaunching", 1)[1].split("buildWindow()", 1)[0]
    assert "installMainMenu()" in main.split("private func rebuildInterface()", 1)[1].split("show(page: page)", 1)[0]


def test_swift_window_accepts_dropped_media_files() -> None:
    main = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift").read_text(encoding="utf-8")
    background = main.split("private final class BlobBackgroundView", 1)[1].split("\nprivate final class", 1)[0]
    assert "registerForDraggedTypes([.fileURL])" in background
    assert "override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation" in background
    assert "override func performDragOperation(_ sender: NSDraggingInfo) -> Bool" in background
    drop = main.split("private func acceptDroppedFiles(_ urls: [URL]) -> Bool", 1)[1].split("\n    }\n", 1)[0]
    assert "conforms(to: .audio)" in drop and "conforms(to: .movie)" in drop
    assert "appendSelectedFiles(" in drop
    # Тот же путь, что и у панели выбора: дедупликация и refreshSelectedFiles().
    chooser = main.split("@objc private func chooseFiles(_ sender: Any?)", 1)[1].split("\n    }\n", 1)[0]
    assert "appendSelectedFiles(panel.urls)" in chooser


def test_tauri_prototype_does_not_persist_hf_token() -> None:
    app = Path("desktop/ui/app.js").read_text(encoding="utf-8")
    assert 'restored.hfToken = ""' in app
    assert "const { hfToken: _secret, ...persisted } = settings" in app


def test_swift_batch_clears_previous_results_and_preserves_failed_keychain_migration():
    main = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift").read_text(encoding="utf-8")
    assert "transcriptionResults.removeAll()" in main
    assert "selectedResultURL = nil" in main
    migration = main.split('if let legacyToken = defaults.string(forKey: "settings.hfToken")', 1)[1].split("cleanupDownloadedMedia()", 1)[0]
    assert "try SecureStore.set" in migration
    assert migration.index("try SecureStore.set") < migration.index('defaults.removeObject(forKey: "settings.hfToken")')
    assert "catch" in migration


def test_dark_theme_overrides_named_light_widgets():
    theme = Path("src/gui/theme_mixin.py").read_text(encoding="utf-8")
    dark = theme.split('if self._theme == "dark":', 1)[1].split("self.setStyleSheet", 1)[0]
    assert "QPlainTextEdit#api_code_editor" in dark
    assert "QTabWidget#result_tabs::pane" in dark
    assert "QLineEdit#settings_path_value" in dark


def test_downloaded_media_cleanup_retains_failed_roots_for_retry() -> None:
    main = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift").read_text(encoding="utf-8")
    cleanup = main.split("private func cleanupDownloadedMedia()", 1)[1].split("@objc private func chooseOutputFolder", 1)[0]
    assert "var failed = Set<URL>()" in cleanup
    assert "failed.insert(root)" in cleanup
    assert "downloadedMediaRoots = failed" in cleanup
    assert "NSLog" in cleanup


def test_appkit_about_uses_bundle_release_version():
    main = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift").read_text(encoding="utf-8")
    about = main.split('case "О приложении":', 1)[1].split("default: break", 1)[0]
    assert 'CFBundleShortVersionString' in about
    assert 'settingsField("Версия приложения"' in about
    assert 'label("1.3.0"' not in about


MAIN_SWIFT = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift")


def _swift_block(source: str, opener: str) -> str:
    """Тело первого объявления `opener { … }` на уровне метода AppController."""
    return source.split(opener, 1)[1].split("\n    }\n", 1)[0]


def test_swift_empty_output_path_falls_back_to_default_folder() -> None:
    # Поле пути сохраняется на каждое нажатие клавиши; стёртое поле оставляло в
    # defaults пустую строку, `?? "~/Documents/GigaAM"` не срабатывал, и кнопка
    # запуска молча выключалась без видимого disabled-состояния.
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    text = _swift_block(main, "private var outputPathText: String {")
    assert 'defaults.string(forKey: "output.path")' in text
    assert "trimmingCharacters(in: .whitespacesAndNewlines)" in text
    assert 'isEmpty ? "~/Documents/GigaAM"' in text
    directory = _swift_block(main, "private var outputDirectory: URL? {")
    assert "(outputPathText as NSString).expandingTildeInPath" in directory
    assert 'defaults.string(forKey: "output.path") ?? "~/Documents/GigaAM"' not in main
    assert main.count('editableText(outputPathText, key: "output.path"') == 2


def test_swift_disabled_primary_button_is_visibly_dimmed() -> None:
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    padded = main.split("private final class PaddedButton: NSButton {", 1)[1].split("\nprivate final class", 1)[0]
    assert "override var isEnabled: Bool" in padded
    assert "alphaValue = isEnabled ? 1 :" in padded


def test_swift_sortformer_never_sends_manual_speaker_count() -> None:
    # Sortformer определяет спикеров сам (до 4); ручное значение 5–6 роняло файл
    # с «Sortformer поддерживает не более 4 спикеров», а 1–4 молча игнорировалось.
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    available = _swift_block(main, "private var manualSpeakerCountAvailable: Bool {")
    assert 'enabledOption("settings.diarization", defaultValue: false)' in available
    assert '!= "sortformer"' in available
    settings = _swift_block(main, "private func transcriptionSettings() -> NativeTranscriptionSettings {")
    assert "settings.numSpeakers = manualSpeakerCountAvailable ?" in settings
    controls = _swift_block(main, "private func refreshProcessingControls() {")
    assert 'key == "processing.speakers"' in controls
    assert "manualSpeakerCountAvailable" in controls
    assert 'if key == "processing.speakers" || key == "settings.diarizationEngine" { control.isEnabled = !busy && enabledOption' not in controls
    assert "Sortformer определяет спикеров автоматически" in main
    # Как в PyQt (_change_diarization_backend → setValue(0)): скрытое ручное
    # значение сбрасывается, а не всплывает позже при смене движка.
    reset = _swift_block(main, "private func resetManualSpeakerCountIfUnavailable() {")
    assert "guard !manualSpeakerCountAvailable" in reset
    assert 'defaults.removeObject(forKey: "processing.speakers")' in reset
    assert "resetManualSpeakerCountIfUnavailable()" in _swift_block(main, "@objc private func popupChanged(_ sender: NSPopUpButton) {")
    assert "resetManualSpeakerCountIfUnavailable()" in _swift_block(main, "@objc private func switchChanged(_ sender: NSButton) {")
    assert 'popup.selectItem(withTitle: "Авто")' in controls


def test_swift_english_dictionary_has_no_duplicate_keys() -> None:
    # Swift traps on a dictionary literal with duplicate keys, and `english` is a
    # lazy static touched only when the UI language is English: 2.0.4 crashed at
    # launch for every English user with «Dictionary literal contains duplicate keys».
    import re

    source = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift").read_text(encoding="utf-8")
    literal = source.split("private static let english: [String: String] = [", 1)[1].split("\n    ]\n", 1)[0]
    keys = re.findall(r'^\s*"((?:[^"\\]|\\.)*)":', literal, flags=re.MULTILINE)
    assert len(keys) > 200
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert duplicates == []


def test_swift_worker_process_is_shared_between_jobs() -> None:
    worker = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/WorkerProcess.swift").read_text(encoding="utf-8")
    assert "final class WorkerProcess" in worker
    assert "final class LineReader" in worker
    assert "enum WorkerRedaction" in worker
    assert "F_SETNOSIGPIPE" in worker
    transcription = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/Transcription.swift").read_text(encoding="utf-8")
    assert "final class LineReader" not in transcription
    assert "credentialPatterns" not in transcription
    assert "WorkerProcess(" in transcription


def test_swift_llm_job_uses_worker_protocol_and_redacts_api_key() -> None:
    job = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/LLMJob.swift").read_text(encoding="utf-8")
    assert '"type": "llm_start"' in job and '"type": "llm_cancel"' in job
    for event in ('"llm_started"', '"llm_chunk"', '"llm_completed"'):
        assert event in job
    assert "WorkerProcess(" in job
    assert 'settings["api_key"]' in job  # secret collected for redaction
    assert "WorkerRedaction.safeText" in job


def test_swift_llm_page_is_wired_to_llm_job() -> None:
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    page = main.split("private func buildLLM(into content: NSStackView)", 1)[1].split("private func buildAPI", 1)[0]
    assert "unavailableButton(" not in page
    assert "LLM-сервис не подключён" not in page
    assert "#selector(runLLM(_:))" in page and "#selector(cancelLLM(_:))" in page
    assert 'popup(["API", "Claude Code", "Codex", "OpenCode", "Pi", "Other"], key: "llm.provider")' in page
    settings = _swift_block(main, "private func llmSettings() throws -> [String: Any] {")
    assert 'SecureStore.string(for: "llmApiKey")' in settings
    assert '"temperature":' in settings and '"claude_path":' in settings and '"other_args":' in settings
    handler = _swift_block(main, "@objc private func runLLM(_ sender: Any?) {")
    assert "LLMJob(request:" in handler
    receive = _swift_block(main, "private func receiveLLMEvent(_ event: LLMJobEvent) {")
    assert "case .chunk" in receive and "case .completed" in receive and "case .failed" in receive
    assert "LLM-сервис не подключён" not in main


def test_swift_llm_api_key_uses_keychain() -> None:
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    text_changed = _swift_block(main, "@objc private func textChanged(_ sender: NSTextField) {")
    assert 'key == "llm.apiKey"' in text_changed
    assert 'SecureStore.set(sender.stringValue.trimmingCharacters(in: .whitespacesAndNewlines), for: "llmApiKey")' in text_changed
    assert 'defaults.set(sender.stringValue, forKey: "llm.apiKey")' not in main
    terminate = _swift_block(main, "func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {")
    assert "llmJob?.terminate()" in terminate


def test_swift_live_capture_uses_avaudioengine_and_screencapturekit() -> None:
    capture = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveCapture.swift").read_text(encoding="utf-8")
    assert "import AVFoundation" in capture and "import ScreenCaptureKit" in capture
    assert "final class MicrophoneCapture" in capture and "final class SystemAudioCapture" in capture
    assert "AVAudioEngine()" in capture and "installTap(onBus: 0" in capture
    assert "SCStreamConfiguration()" in capture and "capturesAudio = true" in capture
    assert "AVAudioConverter(" in capture
    assert "CGRequestScreenCaptureAccess()" in capture and "AVCaptureDevice.requestAccess(for: .audio" in capture
    assert "chunkFrames: Int = 1600" in capture  # 100 ms at 16 kHz
    assert "Int16" in capture


def test_swift_live_session_job_streams_pcm_and_handles_events() -> None:
    job = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveSessionJob.swift").read_text(encoding="utf-8")
    for command in ('"live_start"', '"live_audio"', '"live_pause"', '"live_resume"', '"live_stop"', '"live_ask"', '"live_ask_cancel"', '"live_capture_event"'):
        assert command in job
    for event in ('"live_status"', '"live_partial"', '"live_final"', '"live_stopped"', '"live_answer_chunk"', '"live_answer"'):
        assert event in job
    assert "base64EncodedString()" in job
    assert "maxBufferedChunks" in job  # 5 s backlog guard → overflow
    assert "WorkerProcess(" in job


def test_swift_live_page_is_wired_to_live_session_job() -> None:
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    page = main.split("private func buildLive(into content: NSStackView)", 1)[1].split("private static let llmProviders", 1)[0]
    assert "unavailableButton(" not in page and "unavailableIcon(" not in page
    assert "Захват аудио не подключён" not in page
    assert "#selector(startLive(_:))" in page and "#selector(pauseLive(_:))" in page and "#selector(stopLive(_:))" in page
    assert "#selector(askLive(_:))" in page
    assert "MicrophoneCapture.devices()" in page
    assert 'popup(["pyannote", "onnx", "sortformer"], key: "live.diarizationEngine")' in page
    assert '"live.speakers"' not in main  # live diarization never takes a manual speaker count
    # Diarization titles are too long for a half-width column of the 270 pt
    # parameters card ("Диар. + таймкоды" rendered as "Диар. +"): full rows only.
    for key in ("live.diarize", "live.diarizeTimestamps"):
        row = page.split(f'key: "{key}"', 1)[0].rsplit("\n", 1)[1]
        assert row.strip().startswith("parametersBody.addArrangedSubview(checkbox("), key
    start = _swift_block(main, "@objc private func startLive(_ sender: Any?) {")
    assert "MicrophoneCapture.requestAccess" in start
    assert "SystemAudioCapture.requestAccess()" in start
    launch = _swift_block(main, "private func launchLive(root: URL, withSystem: Bool) {")
    assert "LiveSessionJob(settings:" in launch
    assert "SystemAudioCapture(onEvent:" in launch and "MicrophoneCapture(deviceID:" in launch
    assert "Live и LLM пока не подключены" not in main
    receive = _swift_block(main, "private func receiveLiveEvent(_ event: LiveSessionEvent) {")
    assert "case .partial" in receive and "case .final" in receive and "case .stopped" in receive and "case .failed" in receive
    terminate = _swift_block(main, "func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {")
    assert "liveJob?.terminate()" in terminate
    controls = _swift_block(main, "private func refreshProcessingControls() {")
    assert "liveJob != nil" in controls  # batch and live exclude each other in one worker
    # Первый final включает «Спросить»: кнопка гейтится на liveFinals, а статус
    # после начала записи больше не меняется — без refresh она оставалась выключенной.
    assert "if firstFinal { refreshLiveControls() }" in receive
    job = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveSessionJob.swift").read_text(encoding="utf-8")
    # Ошибка до первого live_status (например, старый companion без live_*) завершает
    # job, иначе UI навсегда остаётся в «Запуск…».
    assert "if !sessionReported || stopping { finish(.failed(message)) }" in job


def test_swift_diarization_formats_are_selectable_and_gated_by_toggle() -> None:
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    processing = main.split("private func buildProcessing(into content: NSStackView)", 1)[1].split("private func buildResult", 1)[0]
    assert 'checkbox("Диаризация (.txt)", key: "output.diarize", defaultValue: false)' in processing
    assert 'checkbox("Диар. + таймкоды", key: "output.diarizeTimestamps", defaultValue: false)' in processing
    formats = _swift_block(main, "private var outputFormats: [String] {")
    assert '("output.diarize", "txt_diarize", false)' in formats
    assert '("output.diarizeTimestamps", "txt_diarize_timecodes", false)' in formats
    assert 'enabledOption("settings.diarization", defaultValue: false)' in formats
    controls = _swift_block(main, "private func refreshProcessingControls() {")
    assert '"output.diarize"' in controls and '"output.diarizeTimestamps"' in controls
    english = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift").read_text(encoding="utf-8")
    assert '"Диаризация (.txt)":' in english and '"Диар. + таймкоды":' in english
