from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pull_requests_have_ci_gate() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "ruff check ." in workflow
    assert "test_release_hardening.py" in workflow
    assert "npm ci" in workflow
    assert "cargo metadata --locked" in workflow
    assert "runs-on: macos-26" in workflow
    assert "swift build -c release" in workflow


def test_tauri_dependencies_are_locked() -> None:
    assert (ROOT / "desktop/package-lock.json").is_file()
    assert (ROOT / "desktop/src-tauri/Cargo.lock").is_file()


def test_api_examples_use_openai_contract() -> None:
    files = [
        "desktop/ui/app.js",
        "desktop/ui/index.html",
        "src/gui/support_surfaces_mixin.py",
        "macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift",
        "macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift",
    ]
    for rel in files:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "/api/v1/" not in text, rel
    for rel in files:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "/v1/audio/transcriptions" in text, rel
        assert "Authorization: Bearer" in text or "Bearer " in text, rel


def test_all_desktop_version_sources_match_release():
    import json

    expected = "2.5.0"
    assert f'__version__ = "{expected}"' in Path("src/__init__.py").read_text(encoding="utf-8")
    assert f'APP_VERSION = "{expected}"' in Path("packaging/_spec_common.py").read_text(encoding="utf-8")
    assert json.loads(Path("desktop/package.json").read_text(encoding="utf-8"))["version"] == expected
    assert json.loads(Path("desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"] == expected
    assert f'version = "{expected}"' in Path("desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    assert f'name = "gigaam-desktop"\nversion = "{expected}"' in Path("desktop/src-tauri/Cargo.lock").read_text(encoding="utf-8")


def test_liquid_release_bundle_contains_configured_icon():
    # Liquid has its own generated icon (scripts/make_liquid_icon.py); the PyQt
    # bundles keep assets/icon.icns.
    workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
    assert 'cp assets/icon-liquid.icns "$APP/Contents/Resources/GigaAMLiquid.icns"' in workflow
    assert 'cp assets/icon.icns "$APP/Contents/Resources/GigaAMLiquid.icns"' not in workflow
    icon = Path("assets/icon-liquid.icns")
    assert icon.is_file() and icon.stat().st_size > 50_000
    assert icon.read_bytes()[:4] == b"icns"
    assert Path("assets/icon-liquid.png").is_file()
    assert '<key>CFBundleIconFile</key><string>GigaAMLiquid.icns</string>' in workflow
    assert 'test -s "$APP/Contents/Resources/GigaAMLiquid.icns"' in workflow


def test_pyqt_about_displays_release_version():
    source = Path("src/gui/ui_build_mixin.py").read_text(encoding="utf-8")
    about = source.split("def _show_about", 1)[1].split("def _make_progress_bar", 1)[0]
    assert "APP_VERSION" in about
    assert "Версия {APP_VERSION}" in about
    assert "Version {APP_VERSION}" in about
