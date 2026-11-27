"""Офлайн-вариант релиза: поиск моделей рядом со сборкой и контракт CI."""

import os
import sys
from pathlib import Path

import pytest

from src.utils.runtime_manager import bundled_hf_cache_dir

WORKFLOW_PATH = Path(".github/workflows/build.yml")
BUILDER_PATH = Path("scripts/build_offline_models.py")


def _make_cache(root, *, repo: str = "models--istupakov--gigaam-v3-onnx"):
    cache = root / "models" / "hf"
    (cache / "hub" / repo / "snapshots" / "abc").mkdir(parents=True)
    (cache / "hub" / repo / "snapshots" / "abc" / "config.json").write_text("{}")
    return cache


def test_no_bundled_cache_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) is None


def test_cache_next_to_executable_is_found(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) == cache


def test_cache_next_to_macos_app_bundle_is_found(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path)
    executable = tmp_path / "GigaAMTranscriber.app" / "Contents" / "MacOS" / "GigaAMTranscriber"
    executable.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(executable))

    assert bundled_hf_cache_dir(frozen=True) == cache


def test_cache_inside_macos_resources_is_found(tmp_path, monkeypatch):
    contents = tmp_path / "GigaAMTranscriber.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    cache = _make_cache(contents / "Resources")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(contents / "MacOS" / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) == cache


def test_cache_beside_companion_nested_in_liquid_bundle_is_found(tmp_path, monkeypatch):
    """Самостоятельный GigaAMLiquid.app везёт companion и модели в Contents/Resources."""
    resources = tmp_path / "GigaAMLiquid.app" / "Contents" / "Resources"
    executable = resources / "GigaAMTranscriber.app" / "Contents" / "MacOS" / "GigaAMTranscriber"
    executable.parent.mkdir(parents=True)
    cache = _make_cache(resources)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(executable))

    assert bundled_hf_cache_dir(frozen=True) == cache


def test_directory_without_hub_is_ignored(tmp_path, monkeypatch):
    """Пустая папка models/hf не должна перехватывать кэш у рабочего каталога."""
    (tmp_path / "models" / "hf").mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) is None


def test_unfrozen_run_looks_at_repository_root(tmp_path, monkeypatch):
    """В обычном запуске из исходников моделей рядом с python быть не должно."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    _make_cache(tmp_path)

    assert bundled_hf_cache_dir(frozen=False) is None


@pytest.mark.parametrize("repo", ["models--istupakov--silero-vad-onnx", "models--x--y"])
def test_any_cached_repository_activates_the_bundle(tmp_path, monkeypatch, repo):
    cache = _make_cache(tmp_path, repo=repo)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) == cache


def test_offline_builder_covers_the_whole_onnx_chain():
    """Офлайн-набор бессмыслен, если хоть одно звено цепочки полезет в сеть."""
    text = BUILDER_PATH.read_text(encoding="utf-8")

    for repo in (
        "models--istupakov--gigaam-v3-onnx",
        "models--istupakov--silero-vad-onnx",
        "models--onnx-community--pyannote-segmentation-3.0",
        "models--wespeaker--wespeaker-voxceleb-resnet34",
    ):
        assert repo in text


def _load_builder():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_offline_models", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_builder_retries_a_transient_hub_failure(monkeypatch):
    """CI v2.3.0: silero VAD не скачался с первой попытки на shared-раннере
    (HF вернул ошибку сети → LocalEntryNotFoundError), и офлайн-сборка Intel
    упала целиком. Одна неудачная попытка — не повод пересобирать релиз."""
    builder = _load_builder()
    calls = {"vad": 0}

    def flaky_vad(**kwargs):
        calls["vad"] += 1
        if calls["vad"] < 3:
            return [], ["silero"]
        return ["silero"], []

    monkeypatch.setattr(builder, "_sleep", lambda seconds: None)
    ok, failed = builder._download_with_retries(lambda: flaky_vad(), what="VAD")

    assert (ok, failed) == (["silero"], [])
    assert calls["vad"] == 3


def test_offline_builder_gives_up_after_the_last_attempt(monkeypatch):
    builder = _load_builder()
    monkeypatch.setattr(builder, "_sleep", lambda seconds: None)

    ok, failed = builder._download_with_retries(lambda: ([], ["silero"]), what="VAD")

    assert failed == ["silero"]


def test_offline_builder_skips_blobs():
    """Копирование вместе с blobs почти удваивает и без того большой архив."""
    text = BUILDER_PATH.read_text(encoding="utf-8")

    assert '("refs", "snapshots")' in text
    assert "symlinks=False" in text


def test_full_liquid_downloads_only_the_pinned_model_files(tmp_path, monkeypatch):
    import huggingface_hub

    from src.utils.model_cache import hf_repo_cache_name

    builder = _load_builder()
    hub = tmp_path / "hub"
    calls = []

    def snapshot_download(*, repo_id, revision, cache_dir, allow_patterns):
        calls.append((repo_id, revision, cache_dir, allow_patterns))
        snapshot = Path(cache_dir) / hf_repo_cache_name(repo_id) / "snapshots" / revision
        snapshot.mkdir(parents=True)
        for name in allow_patterns:
            (snapshot / name).write_bytes(b"model data")
        return str(snapshot)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    builder.fetch_full_liquid_models(hub)
    assert calls == [
        (repo_id, revision, str(hub), list(files))
        for repo_id, revision, files in builder._full_liquid_model_specs()
    ]


def test_full_liquid_rejects_a_download_missing_a_required_file(tmp_path, monkeypatch):
    import huggingface_hub

    builder = _load_builder()
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(builder, "_sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="неполный snapshot.*modeling_gigaam.py"):
        builder.fetch_full_liquid_models(tmp_path / "hub")


def test_direct_full_liquid_download_uses_local_dir_and_publishes_once(tmp_path, monkeypatch):
    import huggingface_hub
    from src.utils.model_cache import hf_repo_cache_name

    builder = _load_builder()
    source = tmp_path / "baseline"
    target = tmp_path / "output" / "hub"
    for repo in builder.OFFLINE_REPOS:
        snapshot = source / repo / "snapshots" / "revision"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_bytes(b"baseline")
    monkeypatch.setattr(builder, "_validate_base_offline_models", lambda hub: None)
    calls = []

    def download(*, repo_id, revision, local_dir, allow_patterns):
        assert not target.exists()
        calls.append((repo_id, revision, allow_patterns))
        snapshot = Path(local_dir)
        snapshot.mkdir(parents=True)
        for name in allow_patterns:
            (snapshot / name).write_bytes(f"{repo_id}/{name}".encode())
        metadata = snapshot / ".cache" / "huggingface"
        metadata.mkdir(parents=True)
        (metadata / "metadata").write_text("temporary")
        return str(snapshot)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    assert builder.build_full_liquid_direct(source, target) > 0
    assert calls == [(repo, revision, list(files)) for repo, revision, files in builder._full_liquid_model_specs()]
    for repo, revision, files in builder._full_liquid_model_specs():
        snapshot = target / hf_repo_cache_name(repo) / "snapshots" / revision
        assert not (snapshot / ".cache").exists()
        for name in files:
            assert (snapshot / name).read_bytes() == f"{repo}/{name}".encode()
    for repo in builder.OFFLINE_REPOS:
        assert (target / repo / "snapshots" / "revision" / "config.json").read_bytes() == b"baseline"
    assert not list(target.parent.glob(".gigaam-full-offline-*"))


def test_direct_full_liquid_failure_cleans_staging_without_publishing(tmp_path, monkeypatch):
    import huggingface_hub

    builder = _load_builder()
    source = tmp_path / "baseline"
    target = tmp_path / "output" / "hub"
    for repo in builder.OFFLINE_REPOS:
        snapshot = source / repo / "snapshots" / "revision"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_bytes(b"baseline")
    monkeypatch.setattr(builder, "_validate_base_offline_models", lambda hub: None)
    monkeypatch.setattr(builder, "_sleep", lambda seconds: None)

    def incomplete(*, repo_id, revision, local_dir, allow_patterns):
        snapshot = Path(local_dir)
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / allow_patterns[0]).write_bytes(b"partial")
        return str(snapshot)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", incomplete)
    with pytest.raises(RuntimeError, match="неполный snapshot"):
        builder.build_full_liquid_direct(source, target)
    assert not target.exists()
    assert not list(target.parent.glob(".gigaam-full-offline-*"))
    assert (source / builder.OFFLINE_REPOS[0] / "snapshots" / "revision" / "config.json").read_bytes() == b"baseline"


def test_direct_full_liquid_refuses_to_replace_an_existing_hub(tmp_path):
    builder = _load_builder()
    target = tmp_path / "output" / "hub"
    target.mkdir(parents=True)
    sentinel = target / "user-data"
    sentinel.write_bytes(b"keep")
    with pytest.raises(SystemExit, match="целевой hub уже существует"):
        builder.build_full_liquid_direct(tmp_path / "baseline", target)
    assert sentinel.read_bytes() == b"keep"


def test_direct_full_liquid_cli_reuses_explicit_baseline_without_fetching(tmp_path, monkeypatch):
    builder = _load_builder()
    source = tmp_path / "baseline" / "hub"
    source.mkdir(parents=True)
    output = tmp_path / "expanded"
    calls = []
    monkeypatch.setattr(builder, "fetch_models", lambda provider: calls.append(("unexpected", provider)))
    monkeypatch.setattr(builder, "fetch_full_liquid_models", lambda hub: calls.append(("unexpected", hub)))
    monkeypatch.setattr(
        builder, "build_full_liquid_direct",
        lambda source_hub, target_hub: calls.append((source_hub, target_hub)) or 0,
    )
    monkeypatch.setattr(sys, "argv", [
        "build_offline_models.py", "--include-transformers-asr", "--direct-expanded-download",
        "--baseline-hub", str(source), "--output", str(output),
    ])
    assert builder.main() == 0
    assert calls == [(source, output / "hub")]


@pytest.mark.parametrize("args", [
    ["--direct-expanded-download"],
    ["--direct-expanded-download", "--include-transformers-asr", "--skip-download"],
    ["--baseline-hub", "somewhere"],
])
def test_direct_full_liquid_rejects_incompatible_options(monkeypatch, args):
    builder = _load_builder()
    monkeypatch.setattr(sys, "argv", ["build_offline_models.py", *args])
    with pytest.raises(SystemExit) as error:
        builder.main()
    assert error.value.code == 2


def test_full_liquid_copies_pinned_snapshots_and_keeps_onnx_assets(tmp_path, monkeypatch):
    from src.utils.model_cache import hf_repo_cache_name

    builder = _load_builder()
    source_hub = tmp_path / "source"
    target_hub = tmp_path / "target" / "hub"
    for repo in builder.OFFLINE_REPOS:
        snapshot = source_hub / repo / "snapshots" / "onnx-revision"
        snapshot.mkdir(parents=True)
        segmentation = repo == "models--onnx-community--pyannote-segmentation-3.0"
        (snapshot / "config.json").write_text('{"model_type":"pyannote"}' if segmentation else "onnx")
        if repo == "models--istupakov--gigaam-v3-onnx":
            names = ("v3_e2e_rnnt_encoder.onnx", "v3_e2e_rnnt_decoder.onnx",
                     "v3_e2e_rnnt_joint.onnx", "v3_e2e_rnnt_vocab.txt")
        elif repo == "models--istupakov--silero-vad-onnx":
            names = ("silero_vad.onnx",)
        elif segmentation:
            names = ("onnx/model.onnx",)
        else:
            names = ("voxceleb_resnet34.onnx",)
        for name in names:
            path = snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"onnx")

    for repo_id, revision, files in builder._full_liquid_model_specs():
        repo = source_hub / hf_repo_cache_name(repo_id)
        snapshot = repo / "snapshots" / revision
        blobs = repo / "blobs"
        snapshot.mkdir(parents=True)
        blobs.mkdir()
        for name in files:
            blob = blobs / name
            blob.write_bytes(f"{repo_id}/{name}".encode())
            (snapshot / name).symlink_to(blob)
        old = repo / "snapshots" / "old-revision"
        old.mkdir()
        (old / "model.safetensors").write_bytes(b"old")

    builder.copy_without_blobs(source_hub, target_hub, include_transformers_asr=True)
    for repo in builder.OFFLINE_REPOS:
        assert (target_hub / repo / "snapshots" / "onnx-revision" / "config.json").is_file()
    for repo_id, revision, files in builder._full_liquid_model_specs():
        repo = target_hub / hf_repo_cache_name(repo_id)
        for name in files:
            copied = repo / "snapshots" / revision / name
            assert copied.read_bytes() == f"{repo_id}/{name}".encode()
            assert not copied.is_symlink()
        assert not (repo / "blobs").exists()
        assert not (repo / "snapshots" / "old-revision").exists()
    # The same resolver used by the frozen worker can find both precisions in
    # each copied snapshot without consulting the network.
    from onnx_asr.loader import create_asr_resolver
    from src.core.asr.models import onnx_model_name, onnx_model_repo
    from src.core.asr.factory import create_backend
    from src.core.asr.onnx_backend import OnnxBackend
    from src.utils import model_cache

    monkeypatch.setattr(model_cache, "bundled_hf_cache_dir", lambda: target_hub.parent)

    for model, (revision, _files) in builder.EXTRA_LIQUID_ONNX_MODELS.items():
        snapshot = target_hub / hf_repo_cache_name(onnx_model_repo(model)) / "snapshots" / revision
        backend, _reason = create_backend(
            "auto", model_name=model, model_revision=model, mlx_repo="unused",
            allow_fallback=True, platform_name="darwin", machine_name="arm64",
            offline=True, repo_is_cached=model_cache.hf_repo_is_cached,
        )
        assert isinstance(backend, OnnxBackend)
        assert backend.model_location().path == snapshot
        for quantization in (None, "int8"):
            resolved = create_asr_resolver(onnx_model_name(model), snapshot, offline=True).resolve_model(
                quantization=quantization,
            )
            expected = f"{model}{'.int8' if quantization else ''}.onnx"
            assert resolved["model"] == snapshot / expected


def test_full_liquid_rejects_incomplete_baseline_onnx_chain(tmp_path):
    builder = _load_builder()
    source = tmp_path / "source"
    for repo in builder.OFFLINE_REPOS:
        snapshot = source / repo / "snapshots" / "revision"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}")
    with pytest.raises(SystemExit, match="неполный offline cache.*gigaam-v3-onnx"):
        builder.copy_without_blobs(source, tmp_path / "output" / "hub", include_transformers_asr=True)


@pytest.mark.parametrize(
    ("missing_index", "missing_name"),
    [(0, "modeling_gigaam.py"), (1, "vad.safetensors"),
     (2, "multilingual_ctc.onnx"), (3, "multilingual_large_ctc.onnx.data")],
)
def test_full_liquid_copy_rejects_incomplete_pinned_snapshot(tmp_path, missing_index, missing_name):
    from src.utils.model_cache import hf_repo_cache_name

    builder = _load_builder()
    source_hub = tmp_path / "source"
    target_hub = tmp_path / "target"
    for index, (repo_id, revision, files) in enumerate(builder._full_liquid_model_specs()):
        snapshot = source_hub / hf_repo_cache_name(repo_id) / "snapshots" / revision
        snapshot.mkdir(parents=True)
        for name in files:
            (snapshot / name).write_bytes(b"" if index == missing_index and name == missing_name else b"ok")

    with pytest.raises(SystemExit, match="неполный snapshot") as error:
        builder._copy_full_liquid_models(source_hub, target_hub)
    assert missing_name in str(error.value)
    assert not target_hub.exists()


@pytest.mark.parametrize("include_transformers", [False, True])
def test_offline_builder_transformers_option_is_opt_in(tmp_path, monkeypatch, include_transformers):
    import src.utils.model_cache as model_cache

    builder = _load_builder()
    hub = tmp_path / "actual-hf-hub-cache"
    hub.mkdir()
    calls = []
    monkeypatch.setattr(model_cache, "hf_hub_cache_dir", lambda: hub)
    monkeypatch.setattr(builder, "fetch_models", lambda provider: calls.append(("onnx", provider)))
    monkeypatch.setattr(builder, "fetch_full_liquid_models", lambda source: calls.append(("transformers", source)))
    monkeypatch.setattr(
        builder, "copy_without_blobs",
        lambda source, target, **kwargs: calls.append(("copy", source, target, kwargs)) or 0,
    )
    args = ["build_offline_models.py", "--output", str(tmp_path / "output")]
    if include_transformers:
        args.append("--include-transformers-asr")
    monkeypatch.setattr(sys, "argv", args)

    assert builder.main() == 0
    assert calls == [
        ("onnx", "cpu"),
        *([("transformers", hub)] if include_transformers else []),
        ("copy", hub, tmp_path / "output" / "hub", {"include_transformers_asr": include_transformers}),
    ]


def test_ci_publishes_offline_variant_for_every_platform():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    for asset in (
        "GigaAMTranscriber-windows-x64-offline.zip",
        "GigaAMTranscriber-macos-arm64-offline.zip",
        "GigaAMTranscriber-linux-x64-offline.zip",
    ):
        assert asset in text

    assert "scripts/build_offline_models.py --output offline/models/hf" in text
    # Офлайн-архивы уезжают в релиз как artifacts, которые релизная джоба
    # выкачивает по маске и публикует целиком — отдельных "Attach ..." шагов
    # больше нет, но контракт обязан оставаться проверяемым.
    assert "name: ${{ matrix.offline_asset }}" in text
    assert "name: ${{ steps.archive_offline.outputs.name }}" in text
    assert "pattern: GigaAM*" in text
    assert "scripts/publish_release_assets.sh \"$GITHUB_REF_NAME\" release-assets" in text


def test_ci_proves_offline_bundle_needs_no_network():
    """Гейт обязателен: без него «офлайн»-сборка молча ушла бы качать модели."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    entrypoint = Path("app.py").read_text(encoding="utf-8")

    assert "--offline-models-smoke" in entrypoint
    assert workflow.count("--offline-models-smoke") >= 2
    assert 'HF_HUB_OFFLINE: "1"' in workflow
    assert "HF_HUB_OFFLINE=1" in workflow


def test_ci_offline_smoke_keeps_bundled_snapshots_with_selected_data_dir():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "GIGAAM_DATA_DIR: ${{ runner.temp }}/gigaam_offline/data" in workflow
    assert 'GIGAAM_DATA_DIR="${RUNNER_TEMP}/gigaam_offline/data"' in workflow


def test_offline_smoke_refuses_a_build_without_bundled_models():
    entrypoint = Path("app.py").read_text(encoding="utf-8")

    assert 'raise RuntimeError("Папка моделей рядом со сборкой не найдена")' in entrypoint
    assert "Офлайн-сборка должна умолчанием выбирать onnx" in entrypoint


def test_ci_smoke_tests_sortformer_onnx_inside_windows_portable():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    entrypoint = Path("app.py").read_text(encoding="utf-8")

    assert "--sortformer-onnx-smoke" in entrypoint
    assert "--sortformer-onnx-smoke" in workflow
    assert "Smoke-test Windows Sortformer ONNX" in workflow


def test_builder_survives_a_windows_console(tmp_path):
    """Консоль Windows — cp1252, и кириллица в выводе роняла сборку моделей."""
    import subprocess

    environment = dict(os.environ, PYTHONIOENCODING="cp1252")
    result = subprocess.run(
        [sys.executable, str(BUILDER_PATH), "--skip-download", "--output", str(tmp_path)],
        capture_output=True,
        env=environment,
    )

    # Кэш приложения в тестовой среде может быть пуст — важно, что падение
    # не про кодировку.
    assert b"UnicodeEncodeError" not in result.stderr


def test_ci_gates_offline_bundle_size():
    """2 ГБ — жёсткий предел ассета GitHub Release, за ним публикация падает."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert text.count("MAX_RELEASE_ASSET_BYTES") >= 4
    assert "Офлайн-архив слишком велик для GitHub Release" in text


def test_config_keeps_huggingface_writable_and_tracks_bundled_cache(tmp_path, monkeypatch):
    """Встроенные модели читаются отдельно, а недостающие качаются в user cache."""
    import src.config as config

    cache = _make_cache(tmp_path)
    writable = tmp_path / "writable-hf"
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setattr(
        "src.utils.runtime_manager.bundled_hf_cache_dir",
        lambda frozen=None: cache,
    )
    monkeypatch.setattr(
        "src.utils.runtime_manager.hf_cache_dir",
        lambda: writable,
    )

    config._setup_huggingface_cache()

    assert Path(os.environ["HF_HOME"]) == writable
    assert config.BUNDLED_MODELS_DIR == cache


def test_offline_bundle_switches_default_backends_to_onnx(tmp_path, monkeypatch):
    """auto выбрал бы MLX или PyTorch, которых в офлайн-наборе нет."""
    import importlib

    import dotenv

    import src.config as config
    from src import data_paths

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(data_paths, "load_data_dir_selection", lambda **kwargs: None)
    monkeypatch.delenv("ASR_BACKEND", raising=False)
    monkeypatch.delenv("DIARIZATION_BACKEND", raising=False)
    cache = _make_cache(tmp_path)
    monkeypatch.setattr(
        "src.utils.runtime_manager.bundled_hf_cache_dir",
        lambda frozen=None: cache,
    )
    try:
        importlib.reload(config)

        assert config.BUNDLED_MODELS_DIR is not None
        assert config.ASR_BACKEND == "onnx"
        assert config.DIARIZATION_BACKEND == "onnx"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_explicit_huggingface_home_still_wins(tmp_path, monkeypatch):
    import src.config as config

    monkeypatch.setenv("HF_HOME", str(tmp_path / "chosen"))
    monkeypatch.setattr(
        "src.utils.runtime_manager.bundled_hf_cache_dir",
        lambda frozen=None: _make_cache(tmp_path),
    )

    config._setup_huggingface_cache()

    assert os.environ["HF_HOME"] == str(tmp_path / "chosen")
