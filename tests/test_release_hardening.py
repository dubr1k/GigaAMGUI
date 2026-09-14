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


def test_tauri_api_examples_match_authenticated_v1_contract() -> None:
    javascript = (ROOT / "desktop/ui/app.js").read_text(encoding="utf-8")
    html = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
    for text in (javascript, html):
        assert "/api/v1/transcribe" in text
        assert "X-API-Key" in text
        assert "enable_diarization" in text
        assert "/api/transcribe" not in text
        assert '"diarize"' not in text
