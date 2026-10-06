"""ONNX_MODEL_DIR — корень моделей, а не каталог одной модели.

Каталог данных (data_paths.apply_data_dir) и Docker (docker-entrypoint.sh)
создают пустой ``<root>/models/onnx`` и передают его в ONNX_MODEL_DIR. onnx-asr
0.12 считает любой существующий ``local_dir`` полным и уходит в офлайн, поэтому
ASR падал на «v3_e2e_rnnt_encoder.onnx not found», сегментация — на
«config.json not found», а config.json разных репозиториев сталкивались в
одном каталоге.

Тесты гоняют настоящий ``onnx_asr`` Resolver; подменяется только сеть
(``huggingface_hub.snapshot_download``/``hf_hub_download``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.asr.onnx_backend import OnnxBackend
from src.core.asr.onnx_vad import OnnxVadSegmenter
from src.core.diarization.onnx_embeddings import OnnxSpeakerEmbeddings
from src.core.diarization.onnx_segmentation import OnnxSegmentation
from src.utils import model_cache
from src.utils.model_cache import onnx_model_location

ASR_REPO = "istupakov/gigaam-v3-onnx"
VAD_REPO = "istupakov/silero-vad-onnx"
SEGMENTATION_REPO = "onnx-community/pyannote-segmentation-3.0"
EMBEDDING_REPO = "wespeaker/wespeaker-voxceleb-resnet34"


class _FakeHub:
    """Сеть Hugging Face: пишет запрошенные файлы и считает загрузки."""

    def __init__(self, cache: Path):
        self.cache = cache
        self.downloads: list[tuple[str, Path | None]] = []

    def _target(self, repo_id: str, local_dir) -> Path:
        if local_dir is not None:
            return Path(local_dir)
        return self.cache / repo_id.replace("/", "--")

    @staticmethod
    def _config(repo_id: str) -> str:
        model_type = "pyannote" if "pyannote" in repo_id else repo_id
        return json.dumps({"model_type": model_type, "repo": repo_id})

    def snapshot_download(self, repo_id, local_dir=None, local_files_only=False, allow_patterns=None):
        target = self._target(repo_id, local_dir)
        if local_files_only:
            if not target.is_dir() or not any(target.iterdir()):
                raise FileNotFoundError(f"{repo_id} is not cached")
            return str(target)
        self.downloads.append((repo_id, Path(local_dir) if local_dir is not None else None))
        target.mkdir(parents=True, exist_ok=True)
        for pattern in allow_patterns or ():
            name = pattern.removeprefix("**/").replace("*", "model")
            if "?" in name or "/" in name:
                continue
            content = self._config(repo_id) if name == "config.json" else "stub"
            (target / name).write_text(content, encoding="utf-8")
        return str(target)

    def hf_hub_download(self, repo_id, filename, local_dir=None, local_files_only=False):
        target = self._target(repo_id, local_dir)
        path = target / filename
        if local_files_only:
            if not path.is_file():
                raise FileNotFoundError(f"{repo_id}/{filename} is not cached")
            return str(path)
        self.downloads.append((repo_id, Path(local_dir) if local_dir is not None else None))
        target.mkdir(parents=True, exist_ok=True)
        path.write_text(self._config(repo_id), encoding="utf-8")
        return str(path)


@pytest.fixture
def hub(monkeypatch, tmp_path):
    import huggingface_hub

    fake = _FakeHub(tmp_path / "hf-cache")
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake.snapshot_download)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake.hf_hub_download)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    # Ни офлайн-набора, ни заполненного HF-кэша.
    monkeypatch.setattr(model_cache, "resolve_bundled_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(model_cache, "hf_repo_is_cached", lambda *_a, **_k: False)
    return fake


def _resolve_with_onnx_asr(kind: str, model: str, path=None, *, offline=None, **_kwargs):
    """Тот же разбор каталога, что делает onnx_asr при загрузке модели."""
    from onnx_asr import loader

    factory = {
        "asr": loader.create_asr_resolver,
        "vad": loader.create_vad_resolver,
        "se": loader.create_se_resolver,
    }[kind]
    resolver = factory(model, path, offline=offline)
    return resolver.resolve_model(quantization=None)


class _TimestampModel:
    def __init__(self, files):
        self.files = files

    def with_timestamps(self):
        return self


class _Vad:
    def __init__(self, files):
        self.files = files
        self._model = self

    def segment_batch(self, *_args):
        return iter(())

    def run(self, *_args):
        return None


def _empty_data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "models" / "onnx"
    root.mkdir(parents=True)
    return root


def test_asr_downloads_into_its_own_subdirectory_of_empty_root(hub, tmp_path):
    root = _empty_data_root(tmp_path)
    backend = OnnxBackend(
        model="v3_e2e_rnnt",
        provider="cpu",
        model_dir=str(root),
        model_factory=lambda model, **kw: _TimestampModel(_resolve_with_onnx_asr("asr", model, **kw)),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )
    messages: list[str] = []

    assert backend.load(logger=messages.append), messages

    encoder = backend.model.files["encoder"]
    assert encoder.parent == root / "istupakov--gigaam-v3-onnx"
    assert hub.downloads == [(ASR_REPO, root / "istupakov--gigaam-v3-onnx")]


def test_asr_resumes_incomplete_managed_directory(hub, tmp_path):
    root = _empty_data_root(tmp_path)
    partial = root / "istupakov--gigaam-v3-onnx"
    partial.mkdir()
    (partial / "config.json").write_text("{}", encoding="utf-8")
    backend = OnnxBackend(
        model="v3_e2e_rnnt",
        provider="cpu",
        model_dir=str(root),
        model_factory=lambda model, **kw: _TimestampModel(_resolve_with_onnx_asr("asr", model, **kw)),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    assert backend.load()
    assert backend.model.files["decoder"].parent == partial


def test_complete_managed_directory_is_used_without_network(hub, tmp_path):
    root = _empty_data_root(tmp_path)
    ready = root / "istupakov--gigaam-v3-onnx"
    ready.mkdir()
    for name in ("encoder", "decoder", "joint"):
        (ready / f"v3_e2e_rnnt_{name}.onnx").write_text("stub", encoding="utf-8")
    (ready / "v3_e2e_rnnt_vocab.txt").write_text("stub", encoding="utf-8")
    backend = OnnxBackend(
        model="v3_e2e_rnnt",
        provider="cpu",
        model_dir=str(root),
        model_factory=lambda model, **kw: _TimestampModel(_resolve_with_onnx_asr("asr", model, **kw)),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    assert backend.load()
    assert hub.downloads == []


def test_legacy_flat_asr_directory_keeps_working(hub, tmp_path):
    flat = tmp_path / "gigaam-v3-onnx"
    flat.mkdir()
    (flat / "config.json").write_text("{}", encoding="utf-8")
    for name in ("encoder", "decoder", "joint"):
        (flat / f"v3_e2e_rnnt_{name}.onnx").write_text("stub", encoding="utf-8")
    (flat / "v3_e2e_rnnt_vocab.txt").write_text("stub", encoding="utf-8")
    backend = OnnxBackend(
        model="v3_e2e_rnnt",
        provider="cpu",
        model_dir=str(flat),
        model_factory=lambda model, **kw: _TimestampModel(_resolve_with_onnx_asr("asr", model, **kw)),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    assert backend.load()
    assert backend.model.files["encoder"].parent == flat
    assert hub.downloads == []
    # VAD не пишет свой подкаталог в чужой каталог модели — уходит в HF-кэш.
    assert onnx_model_location(VAD_REPO, root=flat).path is None


def test_vad_and_diarization_models_do_not_share_one_directory(hub, tmp_path):
    root = _empty_data_root(tmp_path)

    vad = OnnxVadSegmenter(
        model="silero",
        provider="cpu",
        model_dir=str(root),
        vad_factory=lambda model, **kw: _Vad(_resolve_with_onnx_asr("vad", model, **kw)),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )
    silero = vad._ensure_vad().files["model"]

    embeddings = OnnxSpeakerEmbeddings(
        provider="cpu",
        model_dir=str(root),
        model_factory=lambda *, providers, model_dir, **kw: _Vad(
            _resolve_with_onnx_asr("se", EMBEDDING_REPO, model_dir, **kw)
        ),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )
    wespeaker = embeddings._ensure_model().files["model"]

    assert silero.parent == root / "istupakov--silero-vad-onnx"
    assert wespeaker.parent == root / "wespeaker--wespeaker-voxceleb-resnet34"
    assert not (root / "config.json").exists()


def test_segmentation_reads_its_config_from_own_subdirectory(hub, tmp_path, monkeypatch):
    # Тип pyannote-сегментации onnx-asr узнаёт из config.json репозитория.
    # Общий каталог давал «config.json not found» (пустой) или чужой config.
    root = _empty_data_root(tmp_path)
    from src.core.diarization import onnx_segmentation

    calls = []

    def load_vad_model(model, path=None, *, offline=None, providers=None):
        calls.append((path, offline))
        return _Vad(_resolve_with_onnx_asr("vad", model, path, offline=offline))

    monkeypatch.setattr(onnx_segmentation, "load_vad_model", load_vad_model)
    monkeypatch.setattr(onnx_segmentation, "available_onnx_providers", lambda _p: ("CPUExecutionProvider",))

    OnnxSegmentation(provider="cpu", model_dir=str(root))._ensure_session()

    subdir = root / "onnx-community--pyannote-segmentation-3.0"
    assert calls == [(subdir, False)]
    assert json.loads((subdir / "config.json").read_text(encoding="utf-8"))["model_type"] == "pyannote"


def test_offline_mode_does_not_point_onnx_asr_at_empty_root(hub, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    root = _empty_data_root(tmp_path)

    location = onnx_model_location(ASR_REPO, root=root, accept_flat_root=True)

    assert location.path is None


def test_bundled_snapshot_beats_empty_explicit_root(monkeypatch, tmp_path):
    bundled = tmp_path / "bundle" / "snapshot"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(model_cache, "resolve_bundled_snapshot", lambda *_a, **_k: bundled)
    root = _empty_data_root(tmp_path)

    location = onnx_model_location(ASR_REPO, root=root, accept_flat_root=True)

    assert location.path == bundled
    assert location.offline is None


def test_no_explicit_root_keeps_bundled_or_huggingface_cache(monkeypatch):
    monkeypatch.setattr(model_cache, "resolve_bundled_snapshot", lambda *_a, **_k: None)

    assert onnx_model_location(ASR_REPO, root=None) == model_cache.OnnxModelLocation(None)
