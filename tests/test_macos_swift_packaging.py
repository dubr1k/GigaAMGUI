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


def test_release_waits_for_and_downloads_swift_artifact() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "needs: [build, build-macos-full, build-macos-intel, build-macos-swift]" in text
    assert "pattern: GigaAM*" in text


def test_swift_client_documents_python_runtime_requirements() -> None:
    text = Path("macos/GigaAMLiquid/README.md").read_text(encoding="utf-8")
    assert "Python 3.11" in text
    assert "GIGAAM_PROJECT_ROOT" in text
    assert "GIGAAM_PYTHON" in text


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
    assert 'childEnvironment["HF_HUB_OFFLINE"] = "1"' in runtime


def test_tauri_prototype_does_not_persist_hf_token() -> None:
    app = Path("desktop/ui/app.js").read_text(encoding="utf-8")
    assert 'restored.hfToken = ""' in app
    assert "const { hfToken: _secret, ...persisted } = settings" in app
