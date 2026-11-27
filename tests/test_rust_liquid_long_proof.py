"""Regression tests for the long UI proof's oracle, not a packaged-app proof."""

import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "rust_liquid_long_ui_proof", ROOT / "packaging/rust-liquid/long-ui-proof.py"
)
if SPEC is None or SPEC.loader is None:
    raise ImportError("Cannot load the long UI proof")
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)


def test_community_bundle_proof_stages_only_local_assets(tmp_path):
    source = tmp_path / "authorized"
    names = ("export.json", "plda.json", "tract/segmentation.onnx", "tract/embedding.onnx")
    for name in names:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    root = tmp_path / "app-data"
    proof.UI.prepare_community_bundle(root)
    proof.UI.prepare_community_bundle(root, source)
    base = "roaming" if proof.UI.sys.platform == "win32" else "data"
    target = root / base / "ru.gigaam.gui.rustliquid/models/pyannote-community"
    assert all((target / name).read_text() == name for name in names)
    with pytest.raises(RuntimeError, match="already exists"):
        proof.UI.prepare_community_bundle(root, source)


def test_community_bundle_proof_rejects_incomplete_source(tmp_path):
    source = tmp_path / "incomplete"
    source.mkdir()
    with pytest.raises(RuntimeError, match="incomplete"):
        proof.UI.prepare_community_bundle(tmp_path / "app-data", source)


def test_community_download_proof_requires_empty_model_root(tmp_path):
    root = tmp_path / "app-data"
    models = proof.UI._require_empty_model_root(root)
    models.mkdir(parents=True)
    assert proof.UI._require_empty_model_root(root) == models
    existing = models / "user-file"
    existing.write_bytes(b"user data")
    with pytest.raises(RuntimeError, match="empty model root"):
        proof.UI._require_empty_model_root(root)
    assert existing.read_bytes() == b"user data"


def community_test_assets(root):
    assets = []
    for relative, content in (
        ("export.json", b"manifest"),
        ("plda.json", b"plda"),
        ("tract/segmentation.onnx", b"segmentation"),
        ("tract/embedding.onnx", b"embedding"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        assets.append((path.name, relative, len(content), hashlib.sha256(content).hexdigest()))
    return tuple(assets)


def test_community_download_proof_verifies_all_files(tmp_path):
    root = tmp_path / "bundle"
    assets = community_test_assets(root)
    proof.UI.verify_community_assets(root, assets)


@pytest.mark.parametrize("damage", ["size", "hash", "extra", "missing"])
def test_community_download_proof_rejects_changed_files(tmp_path, damage):
    root = tmp_path / "bundle"
    assets = community_test_assets(root)
    manifest = root / "export.json"
    if damage == "size":
        manifest.write_bytes(b"short")
    elif damage == "hash":
        manifest.write_bytes(b"Manifest")
    elif damage == "extra":
        (root / "extra").write_bytes(b"unexpected")
    else:
        manifest.unlink()
    with pytest.raises(RuntimeError):
        proof.UI.verify_community_assets(root, assets)


def test_incomplete_community_proof_removes_only_its_unchanged_bundle(tmp_path):
    root = tmp_path / "app-data"
    with proof.UI.incomplete_community_bundle(root) as target:
        sentinel = target / "export.json"
        assert sentinel.read_bytes() == b"Rust Liquid incomplete Community-1 proof bundle\n"
        assert set(target.iterdir()) == {sentinel}
    assert not target.exists()


def test_incomplete_community_proof_refuses_an_existing_bundle(tmp_path):
    root = tmp_path / "app-data"
    base = "roaming" if proof.UI.sys.platform == "win32" else "data"
    target = root / base / "ru.gigaam.gui.rustliquid/models/pyannote-community"
    target.mkdir(parents=True)
    existing = target / "user-file"
    existing.write_bytes(b"user data")
    with pytest.raises(RuntimeError, match="already exists"):
        with proof.UI.incomplete_community_bundle(root):
            pytest.fail("Existing bundle must not be entered")
    assert existing.read_bytes() == b"user data"


@pytest.mark.parametrize("drift", ["sentinel", "extra"])
def test_incomplete_community_proof_preserves_changed_bundle(tmp_path, drift):
    root = tmp_path / "app-data"
    with pytest.raises(RuntimeError, match="bundle changed"):
        with proof.UI.incomplete_community_bundle(root) as target:
            sentinel = target / "export.json"
            if drift == "sentinel":
                sentinel.write_bytes(b"changed")
            else:
                (target / "extra").write_bytes(b"do not delete")
    assert target.is_dir()
    assert sentinel.read_bytes() == (b"changed" if drift == "sentinel"
                                     else b"Rust Liquid incomplete Community-1 proof bundle\n")
    if drift == "extra":
        assert (target / "extra").read_bytes() == b"do not delete"


def test_incomplete_community_proof_preserves_replaced_directory(tmp_path):
    root = tmp_path / "app-data"
    with pytest.raises(RuntimeError, match="bundle changed"):
        with proof.UI.incomplete_community_bundle(root) as target:
            original = target.with_name("original-bundle")
            target.rename(original)
            target.mkdir()
            (target / "export.json").write_bytes(b"Rust Liquid incomplete Community-1 proof bundle\n")
    assert (original / "export.json").is_file()
    assert (target / "export.json").is_file()


def test_incomplete_community_proof_preserves_bundle_if_proof_fails(tmp_path):
    root = tmp_path / "app-data"
    with pytest.raises(ValueError, match="proof failed"):
        with proof.UI.incomplete_community_bundle(root) as target:
            raise ValueError("proof failed")
    assert (target / "export.json").is_file()


@pytest.mark.parametrize("transcript", [
    "[SPEAKER_UNKNOWN] words [SPEAKER_OVERLAP] words",
    "[SPEAKER_1] words [SPEAKER_01] words",
])
def test_community_proof_rejects_missing_distinct_speakers(transcript):
    with pytest.raises(RuntimeError, match="two distinct numbered speakers"):
        proof.UI.require_community_speakers(transcript)


def test_community_proof_accepts_two_numbered_speakers():
    proof.UI.require_community_speakers("[SPEAKER_0] first\n[SPEAKER_1] second")


def word(start, end, status, speaker=None):
    result = {"start": start, "end": end, "speaker_status": status}
    if speaker is not None:
        result["speaker"] = speaker
    return result


@pytest.fixture
def example():
    # These fixed placements and IDs are an independent input to the *oracle*
    # tests. They are not a recorded result from the packaged application.
    reference = {
        "duration": 1804.0,
        "solo": {"first": (0.0, 1.0), "other": (2.0, 3.0),
                 "return": (4.0, 5.0), "late_return": (1802.0, 1803.0)},
        "speech_spans": [(0.0, 1.0), (2.0, 3.0), (4.0, 5.0),
                         (6.0, 6.5), (7.0, 7.4), (1802.0, 1803.0)],
        "overlap_frames": [(7.0, 7.1), (7.1, 7.2), (7.2, 7.3)],
    }
    words = [
        word(0.1, 0.2, "known", 7), word(0.3, 0.4, "known", 7),
        word(2.1, 2.2, "known", 3), word(2.3, 2.4, "known", 3),
        word(4.1, 4.2, "known", 7), word(4.3, 4.4, "known", 7),
        word(6.1, 6.2, "unknown"), word(7.1, 7.2, "overlap"),
        word(1802.1, 1802.2, "known", 7), word(1802.3, 1802.4, "known", 7),
    ]
    data = {"duration": 1804.0, "text": "some speech", "words": words}
    transcript = "[SPEAKER_7] speech\n\n[SPEAKER_UNKNOWN] speech\n\n[SPEAKER_OVERLAP] speech"
    srt = ("1\n00:00:06,100 --> 00:00:06,200\n[SPEAKER_UNKNOWN] speech\n\n"
           "2\n00:00:07,100 --> 00:00:07,200\n[SPEAKER_OVERLAP] speech\n\n"
           "3\n00:30:02,100 --> 00:30:02,400\nspeech\n")
    return data, transcript, srt, reference


def test_offline_public_source_excerpts_match_pinned_pcm():
    clips = proof.load_sources(ROOT / "tests/fixtures/rust_liquid_long_sources.json")
    assert set(clips) == {
        "2035-147960-0005", "2035-147960-0006",
        "7976-110124-0023", "7976-110124-0024",
        "1673-143397-0002", "1673-143397-0003",
    }
    assert all(0 < len(pcm) <= 4 * 16_000 for pcm in clips.values())


def test_oracle_accepts_consistent_return_overlap_and_absolute_times(example):
    data, transcript, srt, reference = example
    assert proof.validate_export(data, transcript, srt, reference) == {
        "first": 7, "other": 3, "return": 7, "late_return": 7,
    }


def test_oracle_rejects_lost_speaker_return(example):
    data, transcript, srt, reference = example
    for item in data["words"][-2:]:
        item["speaker"] = 3
    with pytest.raises(ValueError, match="Speaker return or separation"):
        proof.validate_export(data, transcript, srt, reference)


def test_oracle_rejects_distinct_speakers_merged(example):
    data, transcript, srt, reference = example
    for item in data["words"][2:4]:
        item["speaker"] = 7
    with pytest.raises(ValueError, match="Speaker return or separation"):
        proof.validate_export(data, transcript, srt, reference)


def test_oracle_rejects_a_speaker_tie(example):
    data, transcript, srt, reference = example
    data["words"][1]["speaker"] = 9
    with pytest.raises(ValueError, match="no majority speaker"):
        proof.validate_export(data, transcript, srt, reference)


@pytest.mark.parametrize("index", [6, 7])
def test_oracle_rejects_speaker_id_on_unknown_or_overlap(example, index):
    data, transcript, srt, reference = example
    data["words"][index]["speaker"] = 7
    with pytest.raises(ValueError, match="incorrectly assigned speaker"):
        proof.validate_export(data, transcript, srt, reference)


def test_oracle_rejects_missing_overlap_status_even_if_mix_has_unknown(example):
    data, transcript, srt, reference = example
    data["words"][7]["speaker_status"] = "unknown"
    with pytest.raises(ValueError, match="Expected known, unknown and overlap"):
        proof.validate_export(data, transcript, srt, reference)


def test_oracle_rejects_forced_identity_in_simultaneous_audio(example):
    data, transcript, srt, reference = example
    data["words"].insert(8, word(7.21, 7.28, "known", 7))
    with pytest.raises(ValueError, match="forced to one speaker"):
        proof.validate_export(data, transcript, srt, reference)


def test_oracle_rejects_overlap_label_outside_simultaneous_audio(example):
    data, transcript, srt, reference = example
    data["words"][7]["speaker_status"] = "unknown"
    data["words"].insert(7, word(6.3, 6.4, "overlap"))
    with pytest.raises(ValueError, match="No such word was marked overlap"):
        proof.validate_export(data, transcript, srt, reference)


@pytest.mark.parametrize("bad_start,bad_end", [(1801.0, 1800.9), (float("nan"), 1802.0), (1805.0, 1805.1)])
def test_oracle_rejects_invalid_word_timestamps(example, bad_start, bad_end):
    data, transcript, srt, reference = example
    data["words"][-1]["start"] = bad_start
    data["words"][-1]["end"] = bad_end
    with pytest.raises(ValueError, match="word timestamp"):
        proof.validate_export(data, transcript, srt, reference)


def test_oracle_rejects_srt_without_late_cue(example):
    data, transcript, srt, reference = example
    srt = srt.replace("00:30:02,100 --> 00:30:02,400", "00:00:08,100 --> 00:00:08,400")
    with pytest.raises(ValueError, match="SRT lacks a cue"):
        proof.validate_export(data, transcript, srt, reference)


def test_oracle_rejects_missing_export_and_visible_labels(example):
    data, transcript, srt, reference = example
    with pytest.raises(ValueError, match="lacks \\[SPEAKER_OVERLAP\\]"):
        proof.validate_export(data, transcript.replace("[SPEAKER_OVERLAP]", ""), srt, reference)
    with pytest.raises(ValueError, match="SRT does not distinguish"):
        proof.validate_export(copy.deepcopy(data), transcript, srt.replace("[SPEAKER_UNKNOWN]", ""), reference)
