"""Проверки конфигурации сборки macOS + MLX."""

from pathlib import Path

SPEC_PATH = Path("packaging/gigaam_app_mac.spec")
REQUIREMENTS_PATH = Path("requirements-macos-mlx.txt")
HOOK_PATH = Path("pyinstaller_hooks/hook-gigaam_mlx.py")
NEMO_HOOK_PATH = Path("pyinstaller_hooks/hook-nemo.py")
WORKFLOW_PATH = Path(".github/workflows/build.yml")


def test_macos_mlx_requirements_pinned_to_known_commit():
    text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    assert "20276ddd6173d636b37c6c6e13b4ee8f7b94d1ac" in text
    assert "gigaam-mlx" in text


def test_spec_includes_mlx_packages():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert "\"mlx\"" in text
    assert "\"gigaam_mlx\"" in text
    assert '"CFBundleShortVersionString": "1.3.3"' in text
    assert '"CFBundleVersion": "1.3.3"' in text


def test_spec_can_bundle_sortformer_runtime():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert "GIGAAM_BUNDLE_SORTFORMER" in text
    assert '"nemo.collections.asr"' in text
    assert 'if not bundle_sortformer:' in text
    assert 'excluded_modules.append("IPython")' in text


def test_spec_bundles_local_models_only_when_explicitly_requested():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert 'os.environ.get("GIGAAM_BUNDLE_MODELS", "")' in text
    assert "if bundle_models:" in text
    assert "datas.append((bundled_gigaam_dir, \"models/gigaam\"))" in text


def test_hook_exists_and_collects_gigaam_mlx():
    text = HOOK_PATH.read_text(encoding="utf-8")
    assert "collect_all(\"gigaam_mlx\")" in text
    assert "sentencepiece" in text


def test_nemo_hook_collects_source_for_torchscript():
    text = NEMO_HOOK_PATH.read_text(encoding="utf-8")
    assert 'module_collection_mode = {"nemo": "pyz+py"}' in text


def test_build_script_prefers_active_conda_environment():
    text = Path("packaging/build_exe_mac.sh").read_text(encoding="utf-8")
    assert "GIGAAM_BUILD_PYTHON" in text
    assert "CONDA_PREFIX" in text
    assert 'PYTHON="$CONDA_PREFIX/bin/python"' in text


def test_build_script_bundles_sortformer_by_default():
    text = Path("packaging/build_exe_mac.sh").read_text(encoding="utf-8")
    assert 'GIGAAM_BUNDLE_SORTFORMER="${GIGAAM_BUNDLE_SORTFORMER:-1}"' in text


def test_build_script_calls_verifier_if_bundle_present():
    text = Path("packaging/build_exe_mac.sh").read_text(encoding="utf-8")
    verifier = Path("scripts/verify_macos_bundle.py").read_text(encoding="utf-8")
    assert "scripts/verify_macos_bundle.py dist/GigaAMTranscriber.app" in text
    assert "--asr-runtime-smoke" in verifier
    assert "--upgrade" not in text  # версия pyinstaller фиксируется вне команды сборки


def test_ci_builds_and_publishes_full_app_zip():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "build-macos-full:" in text
    assert "bash packaging/build_exe_mac.sh" in text
    assert "ditto -c -k --sequesterRsrc --keepParent" in text
    assert 'unzip -tq "$ARCHIVE"' in text
    assert "MAX_RELEASE_ASSET_BYTES" in text
    assert "CFBundleShortVersionString" in text
    assert "Attach full app to release" in text
    assert "requirements-sortformer.txt" in text
    assert 'GIGAAM_BUNDLE_SORTFORMER: "1"' in text


def test_bundle_verifier_smokes_sortformer_runtime_when_requested():
    verifier = Path("scripts/verify_macos_bundle.py").read_text(encoding="utf-8")
    entrypoint = Path("app.py").read_text(encoding="utf-8")
    assert "GIGAAM_BUNDLE_SORTFORMER" in verifier
    assert "--sortformer-runtime-smoke" in verifier
    assert "--sortformer-runtime-smoke" in entrypoint
