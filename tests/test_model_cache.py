from pathlib import Path

from src.utils.model_cache import (
    hf_repo_cache_name,
    resolve_bundled_snapshot,
    resolve_model_dir,
)


def _snapshot(root: Path, repo_id: str, revision: str = "abc") -> Path:
    repo = root / "hub" / hf_repo_cache_name(repo_id)
    snapshot = repo / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(revision, encoding="utf-8")
    return snapshot


def test_hf_repo_cache_name_matches_huggingface_layout():
    assert hf_repo_cache_name("onnx-community/pyannote-segmentation-3.0") == (
        "models--onnx-community--pyannote-segmentation-3.0"
    )


def test_bundled_snapshot_follows_main_ref(tmp_path):
    expected = _snapshot(tmp_path, "org/model", revision="pinned")

    assert resolve_bundled_snapshot("org/model", bundled_root=tmp_path) == expected


def test_bundled_snapshot_falls_back_to_only_complete_snapshot(tmp_path):
    repo = tmp_path / "hub" / hf_repo_cache_name("org/model") / "snapshots"
    (repo / "partial").mkdir(parents=True)
    complete = repo / "complete"
    complete.mkdir()
    (complete / "model.onnx").write_bytes(b"onnx")

    assert resolve_bundled_snapshot("org/model", bundled_root=tmp_path) == complete


def test_missing_bundled_repo_returns_none_so_user_cache_can_download(tmp_path):
    _snapshot(tmp_path, "org/other")

    assert resolve_bundled_snapshot("org/missing", bundled_root=tmp_path) is None
    assert resolve_model_dir("org/missing", bundled_root=tmp_path) is None


def test_explicit_model_directory_wins_over_bundle(tmp_path):
    explicit = tmp_path / "explicit"
    bundled = tmp_path / "bundled"
    explicit.mkdir()
    _snapshot(bundled, "org/model")

    assert resolve_model_dir(
        "org/model",
        explicit=explicit,
        bundled_root=bundled,
    ) == explicit
