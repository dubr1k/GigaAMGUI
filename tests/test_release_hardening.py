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


def _read_surface(rel: str) -> str:
    """A file, or — for a directory such as the Liquid app target — all its Swift sources."""
    path = ROOT / rel
    if path.is_dir():
        return "\n".join(file.read_text(encoding="utf-8") for file in sorted(path.rglob("*.swift")))
    return path.read_text(encoding="utf-8")


def test_api_examples_use_openai_contract() -> None:
    files = [
        "desktop/ui/app.js",
        "desktop/ui/index.html",
        "src/gui/support_surfaces_mixin.py",
        # The Liquid API page may live in any file of the app target.
        "macos/GigaAMLiquid/Sources/GigaAMLiquid",
        "macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift",
    ]
    for rel in files:
        text = _read_surface(rel)
        assert "/api/v1/" not in text, rel
    for rel in files:
        text = _read_surface(rel)
        assert "/v1/audio/transcriptions" in text, rel
        assert "Authorization: Bearer" in text or "Bearer " in text, rel


def test_all_desktop_version_sources_match_release():
    import ast
    import json

    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    from src import __version__

    spec = ast.parse((ROOT / "packaging/_spec_common.py").read_text(encoding="utf-8"))
    bundle_version = next(
        ast.literal_eval(node.value)
        for node in spec.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets)
    )
    package = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((ROOT / "desktop/package-lock.json").read_text(encoding="utf-8"))
    tauri = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((ROOT / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    cargo_lock = tomllib.loads((ROOT / "desktop/src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    locked_app = next(package for package in cargo_lock["package"] if package["name"] == "gigaam-desktop")
    assert {
        bundle_version, package["version"], npm_lock["version"], npm_lock["packages"][""]["version"],
        tauri["version"], cargo["package"]["version"], locked_app["version"],
    } == {__version__}


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


def _spec_common_literal(name: str) -> str:
    import ast

    spec = ast.parse((ROOT / "packaging/_spec_common.py").read_text(encoding="utf-8"))
    return next(
        ast.literal_eval(node.value)
        for node in spec.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    )


def expected_bundle_build_version(release: str) -> str:
    """CFBundleVersion = MAJOR.MINOR.(PATCH*10 + номер пересборки из «-N»)."""
    import re

    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-(\d))?", release)
    assert match, f"unexpected release version {release!r}"
    major, minor, patch, rebuild = match.groups()
    return f"{major}.{minor}.{int(patch) * 10 + int(rebuild or 0)}"


def test_bundle_build_version_follows_release_version():
    # APP_BUILD_VERSION остаётся литералом (CI и install_liquid_local.sh читают
    # его sed'ом), но бампается руками — забытый бамп выпустил бы два релиза с
    # одинаковым CFBundleVersion, и macOS не заменил бы старую копию.
    release = _spec_common_literal("APP_VERSION")
    assert _spec_common_literal("APP_BUILD_VERSION") == expected_bundle_build_version(release)


def test_build_version_scheme_matches_history_and_ci_accepts_it():
    import re

    history = {"2.5.3-2": "2.5.32", "2.5.3-3": "2.5.33", "2.5.4": "2.5.40", "2.5.7": "2.5.70"}
    assert {release: expected_bundle_build_version(release) for release in history} == history
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    pattern = re.search(r'\[\[ "\$BUILD_VERSION" =~ (\S+) \]\]', workflow).group(1)
    # Патч 10+ даёт трёхзначный третий компонент (2.5.10 → 2.5.100).
    for build in ("2.5.70", "2.5.100", "2.10.995"):
        assert re.fullmatch(pattern, build), (pattern, build)
