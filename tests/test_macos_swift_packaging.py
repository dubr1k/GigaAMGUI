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
    assert 'test ! -e "stage/$PRODUCT/app.py"' in text
    assert 'test ! -e "stage/$PRODUCT/src"' in text
    assert 'test ! -e "$ROOT/app.py"' in text
    assert 'test ! -e "$ROOT/src"' in text


def test_offline_swift_archive_runs_a_real_file_through_the_companion() -> None:
    # pong не ловит ни колесо scipy, которое dyld не грузит, ни auto-backend,
    # уходящий офлайн за MLX-моделью: гейт гоняет файл с пустым кэшем и
    # HF_HUB_OFFLINE=1 — ровно так companion запускает GigaAMLiquid.
    text = WORKFLOW.read_text(encoding="utf-8")
    offline = text.split("Assemble and verify offline native Swift archive", 1)[1].split("Upload offline native Swift artifact", 1)[0]
    assert "python3 scripts/native_worker_smoke.py" in offline
    assert 'HF_HOME="${RUNNER_TEMP}/liquid-offline-smoke-hf" HF_HUB_OFFLINE=1' in offline
    assert "--backend" not in offline  # auto, как у пользователя по умолчанию


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


def test_swift_job_launch_does_not_require_source_tree_with_frozen_companion() -> None:
    # Release archives ship GigaAMLiquid.app + GigaAMTranscriber.app and no src/;
    # the legacy `python -m src.tui_worker` guard must not run in companion mode.
    transcription = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/Transcription.swift").read_text(encoding="utf-8")
    launch = transcription.split("private func launch() throws {", 1)[1].split("let task = Process()", 1)[0]
    assert "let runtime = try PythonRuntime.resolve()" in launch
    assert 'guard runtime.frozenCompanion || manager.isReadableFile(atPath: runtime.root.appendingPathComponent("src/tui_worker.py").path)' in launch


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
