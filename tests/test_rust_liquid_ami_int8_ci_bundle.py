"""Exercise AMI proof construction with synthetic, locally generated inputs only."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
sf = pytest.importorskip("soundfile")
torch = pytest.importorskip("torch")
torchaudio = pytest.importorskip("torchaudio")

from tests.test_rust_liquid_int8_ci_bundle import _make_source, _pin_fixture, _save_graph


SCRIPT = Path(__file__).resolve().parents[1] / "packaging/rust-liquid/ami_int8_ci_bundle.py"


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _array_hash(array):
    return hashlib.sha256(array.astype("<f4", copy=False).tobytes(order="C")).hexdigest()


@pytest.fixture
def producer():
    spec = importlib.util.spec_from_file_location("ami_int8_ci_bundle", SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load the AMI proof bundle producer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _segmentation_with_boundary_masks(path):
    # Class order is silence, speaker 0, speaker 1, speaker 2, and overlaps.
    # Speaker 0 and 2 have exactly 20 clean frames; speaker 1 has only 19.
    labels = np.zeros(589, dtype=np.int64)
    labels[:20] = 1
    labels[20:39] = 2
    labels[39:59] = 3
    labels[59:100] = 4
    labels[100:140] = 5
    labels[140:180] = 6
    scores = np.eye(7, dtype=np.float32)[labels][None, :, :]
    helper, tensor, numpy_helper = onnx.helper, onnx.TensorProto, onnx.numpy_helper
    _save_graph(
        path,
        [
            helper.make_node("ReduceMean", ["waveforms"], ["mean"], axes=[1, 2], keepdims=1),
            helper.make_node("Add", ["mean", "scores_base"], ["scores"]),
        ],
        [helper.make_tensor_value_info("waveforms", tensor.FLOAT, [1, 1, 160000])],
        helper.make_tensor_value_info("scores", tensor.FLOAT, [1, 589, 7]),
        [numpy_helper.from_array(scores, "scores_base")],
    )


@pytest.fixture
def fixture_bundle(tmp_path, monkeypatch, producer):
    source = _make_source(tmp_path, producer)
    _segmentation_with_boundary_masks(source / "tract/segmentation.onnx")
    manifest_path = source / "export.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime_sha256"]["tract/segmentation.onnx"] = _hash(source / "tract/segmentation.onnx")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _pin_fixture(monkeypatch, producer, source)

    monkeypatch.setattr(producer, "WINDOW_STARTS", (0, 10, 20, 30))
    times = np.arange(160000, dtype=np.float32) / 16000
    tone = np.sin(2 * np.pi * 220 * times).astype(np.float32)
    samples = np.concatenate([tone * (0.1 + index * 0.03) for index in range(4)])
    audio = tmp_path / "synthetic.wav"
    sf.write(str(audio), samples, 16000, subtype="PCM_16")
    monkeypatch.setattr(producer, "AUDIO_SIZE", audio.stat().st_size)
    monkeypatch.setattr(producer, "AUDIO_SHA256", _hash(audio))
    return producer, source, audio, tmp_path / "output"


def test_rejects_wrong_audio_hash_before_any_parser(tmp_path, monkeypatch, producer):
    source = tmp_path / "source"
    source.mkdir()
    audio = tmp_path / "wrong.wav"
    audio.write_bytes(b"not an AMI WAV")
    monkeypatch.setattr(producer, "AUDIO_SIZE", audio.stat().st_size)

    def forbidden(*args, **kwargs):
        pytest.fail("unverified audio reached a parser")

    monkeypatch.setattr(producer.sf, "SoundFile", forbidden)
    monkeypatch.setattr(producer._support, "_read_json", forbidden)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="hash mismatch"):
        producer.build_bundle(source, audio, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".output-*"))


def test_rejects_bad_pinned_model_before_audio_parsing(fixture_bundle, monkeypatch):
    producer, source, audio, output = fixture_bundle
    model = source / "tract/segmentation.onnx"
    contents = model.read_bytes()
    model.write_bytes(bytes([contents[0] ^ 0xFF]) + contents[1:])
    monkeypatch.setattr(producer.sf, "SoundFile", lambda *a, **k: pytest.fail("audio was parsed"))
    with pytest.raises(ValueError, match="hash mismatch"):
        producer.build_bundle(source, audio, output)
    assert not output.exists()
    assert not list(output.parent.glob(".output-*"))


def test_actual_frontend_masks_cli_and_provenance(fixture_bundle, monkeypatch, capsys):
    producer, source, audio, output = fixture_bundle
    source_hashes = {name: _hash(source / name) for name in producer.PINNED_ASSETS}
    captured = {}
    run = subprocess.run

    def capture_batches(command, **kwargs):
        if command[:2] != [sys.executable, str(producer.QUANTIZE_SCRIPT)]:
            return run(command, **kwargs)
        assert command[:3] == [sys.executable, str(producer.QUANTIZE_SCRIPT), "--model-dir"]
        assert command[4] == "--calibration-dir" and command[6] == "--output-dir"
        assert kwargs["check"] is True and kwargs["env"]["OMP_NUM_THREADS"] == "2"
        snapshot, calibration, staging = map(Path, (command[3], command[5], command[7]))
        assert snapshot != source and snapshot.parent == calibration.parent == staging.parent
        assert {name: _hash(snapshot / name) for name in source_hashes} == source_hashes
        for kind in ("segmentation", "embedding"):
            for path in sorted((calibration / kind).glob("*.npz")):
                with np.load(path, allow_pickle=False) as archive:
                    arrays = {name: archive[name].copy() for name in archive.files}
                captured[path.relative_to(calibration).as_posix()] = (_hash(path), arrays)
        return run(command, **kwargs)

    monkeypatch.setattr(producer.subprocess, "run", capture_batches)
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--source", str(source), "--audio", str(audio), "--output", str(output),
    ])
    producer.main()

    expected_names = {f"segmentation/{start}.npz" for start in producer.WINDOW_STARTS}
    expected_names |= {
        f"embedding/{start}-{speaker}.npz"
        for start in producer.WINDOW_STARTS for speaker in (0, 2)
    }
    assert set(captured) == expected_names
    masks = {0: np.arange(0, 20), 2: np.arange(39, 59)}
    with sf.SoundFile(str(audio)) as sound:
        for start in producer.WINDOW_STARTS:
            sound.seek(start * 16000)
            expected_waveform = sound.read(160000, dtype="float32").reshape(1, 1, -1)
            waveform = captured[f"segmentation/{start}.npz"][1]["waveforms"]
            np.testing.assert_array_equal(waveform, expected_waveform)
            # This is the original per-waveform torchaudio frontend, independently
            # evaluated without vmap, including the checkpoint's centering.
            with torch.no_grad():
                expected_fbank = torchaudio.compliance.kaldi.fbank(
                    torch.from_numpy(waveform[0]) * 32768, **producer.FBANK_OPTIONS
                )
                expected_fbank -= expected_fbank.mean(dim=0, keepdim=True)
            for speaker in (0, 2):
                arrays = captured[f"embedding/{start}-{speaker}.npz"][1]
                np.testing.assert_allclose(arrays["fbank"][0], expected_fbank.numpy(), rtol=1e-6, atol=1e-5)
                mask = arrays["mask"]
                assert mask.shape == (1, 589) and mask.dtype == np.float32
                np.testing.assert_array_equal(np.flatnonzero(mask[0]), masks[speaker])
                assert not np.any(mask[0, 59:180])  # no overlap class is accepted

    manifest = json.loads((output / "export.json").read_text(encoding="utf-8"))
    proof = json.loads((output / "proof-manifest.json").read_text(encoding="utf-8"))
    output_hashes = {name: _hash(output / name) for name in producer.PINNED_ASSETS}
    assert {name: record["sha256"] for name, record in proof["source_files"].items()} == source_hashes
    assert proof["output_files_sha256"] == output_hashes
    assert manifest["runtime_sha256"] == {name: output_hashes[name] for name in producer._support.RUNTIME_FILES}
    assert manifest["quantization"]["source_export_files_sha256"] == source_hashes
    assert proof["proof_only"] is True
    assert proof["calibration"]["synthetic"] is False
    assert proof["calibration"]["source"]["sha256"] == _hash(audio)
    assert proof["calibration"]["source"]["license"] == "CC-BY-4.0"
    assert proof["calibration"]["powerset_class_bits"] == [0, 1, 2, 4, 3, 5, 6]
    assert proof["calibration"]["minimum_single_speaker_frames"] == 20
    assert proof["calibration"]["window_starts_seconds"] == list(producer.WINDOW_STARTS)
    for name, (file_hash, arrays) in captured.items():
        record = proof["calibration"]["files"][name]
        assert record["sha256"] == file_hash
        for input_name, array in arrays.items():
            assert record["inputs"][input_name] == {
                "dtype": "float32", "shape": list(array.shape), "sha256": _array_hash(array),
            }
        if name.startswith("embedding/"):
            assert record["single_speaker_frames"] == 20
    for kind in ("segmentation", "embedding"):
        records = manifest["quantization"]["calibration"][kind]
        assert [item["sha256"] for item in records] == [
            captured[name][0] for name in sorted(captured) if name.startswith(f"{kind}/")
        ]
    assert proof["reference_comparison"]["result"] == "mismatch"
    assert proof["reference_comparison"]["embedding_sha256"] == output_hashes["tract/embedding.onnx"]
    assert proof["reference_comparison"]["reference_embedding_sha256"] == producer.REFERENCE_EMBEDDING_SHA256
    assert proof["versions"]["torch"] == torch.__version__
    assert proof["versions"]["torchaudio"] == torchaudio.__version__
    assert proof["versions"]["soundfile"] == sf.__version__
    assert proof["validation"]["quality_speed_tract_and_release_established"] is False
    assert {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()} == (
        set(source_hashes) | {f"fp32/{name}" for name in source_hashes} | {"proof-manifest.json"}
    )
    stdout = capsys.readouterr().out
    assert f"embedding_sha256={output_hashes['tract/embedding.onnx']}" in stdout
    assert f"proof_manifest_sha256={_hash(output / 'proof-manifest.json')}" in stdout
    assert "embedding_reference_match=mismatch" in stdout
    assert not list(output.parent.glob(".output-*"))


def test_no_qualifying_masks_does_not_publish(fixture_bundle, monkeypatch):
    producer, source, audio, output = fixture_bundle
    # The existing synthetic segmentation graph emits a tie, so argmax is silence.
    from tests.test_rust_liquid_int8_ci_bundle import _segmentation

    _segmentation(source / "tract/segmentation.onnx")
    manifest_path = source / "export.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime_sha256"]["tract/segmentation.onnx"] = _hash(source / "tract/segmentation.onnx")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _pin_fixture(monkeypatch, producer, source)
    monkeypatch.setattr(producer.subprocess, "run", lambda *a, **k: pytest.fail("quantizer was called"))
    with pytest.raises(ValueError, match="no single-speaker calibration masks"):
        producer.build_bundle(source, audio, output)
    assert not output.exists()
    assert not list(output.parent.glob(".output-*"))


@pytest.mark.parametrize("kind", ["file", "empty-directory", "nonempty-directory", "dangling-symlink"])
def test_existing_destination_is_never_replaced(fixture_bundle, kind):
    producer, source, audio, output = fixture_bundle
    if kind == "file":
        output.write_bytes(b"keep")
    elif kind == "dangling-symlink":
        try:
            output.symlink_to(output.parent / "missing", target_is_directory=True)
        except OSError:
            pytest.skip("symbolic links are not available")
    else:
        output.mkdir()
        if kind == "nonempty-directory":
            (output / "keep").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        producer.build_bundle(source, audio, output)
    if kind == "file":
        assert output.read_bytes() == b"keep"
    elif kind == "dangling-symlink":
        assert output.is_symlink()
    elif kind == "empty-directory":
        assert list(output.iterdir()) == []
    else:
        assert (output / "keep").read_bytes() == b"keep"


@pytest.mark.parametrize("interrupt", [False, True])
def test_quantizer_failure_cleans_private_work(fixture_bundle, monkeypatch, interrupt):
    producer, source, audio, output = fixture_bundle
    monkeypatch.setattr(producer, "_create_calibration", lambda *args: ({}, {}))

    def fail_cli(command, **kwargs):
        staging = Path(command[-1])
        staging.mkdir()
        (staging / "partial").write_bytes(b"partial")
        if interrupt:
            raise KeyboardInterrupt
        raise subprocess.CalledProcessError(2, command)

    monkeypatch.setattr(producer.subprocess, "run", fail_cli)
    with pytest.raises((subprocess.CalledProcessError, KeyboardInterrupt)):
        producer.build_bundle(source, audio, output)
    assert not output.exists()
    assert not list(output.parent.glob(".output-*"))


def test_tampered_cli_runtime_is_not_published(fixture_bundle, monkeypatch):
    producer, source, audio, output = fixture_bundle
    run = subprocess.run

    def tamper_cli(command, **kwargs):
        result = run(command, **kwargs)
        (Path(command[-1]) / "tract/segmentation.onnx").write_bytes(b"tampered")
        return result

    monkeypatch.setattr(producer.subprocess, "run", tamper_cli)
    with pytest.raises(ValueError, match="CLI runtime hash mismatch"):
        producer.build_bundle(source, audio, output)
    assert not output.exists()
    assert not list(output.parent.glob(".output-*"))


def test_publication_does_not_replace_a_racing_directory(fixture_bundle, monkeypatch):
    producer, source, audio, output = fixture_bundle
    rename = producer._support._rename_noreplace

    def race(staging, destination):
        destination.mkdir()
        (destination / "keep").write_bytes(b"other writer")
        rename(staging, destination)

    monkeypatch.setattr(producer._support, "_rename_noreplace", race)
    with pytest.raises(FileExistsError):
        producer.build_bundle(source, audio, output)
    assert (output / "keep").read_bytes() == b"other writer"
    assert sorted(path.name for path in output.iterdir()) == ["keep"]
    assert not list(output.parent.glob(".output-*"))


def test_matching_audio_symlink_is_rejected(fixture_bundle):
    producer, source, audio, output = fixture_bundle
    link = output.parent / "audio-link.wav"
    try:
        link.symlink_to(audio)
    except OSError:
        pytest.skip("symbolic links are not available")
    with pytest.raises(ValueError, match="symbolic link"):
        producer.build_bundle(source, link, output)
    assert audio.is_file()
    assert not output.exists()
    assert not list(output.parent.glob(".output-*"))


def test_matching_model_symlink_is_rejected(fixture_bundle):
    producer, source, audio, output = fixture_bundle
    model = source / "tract/segmentation.onnx"
    outside = output.parent / "outside.onnx"
    model.rename(outside)
    try:
        model.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are not available")
    with pytest.raises(ValueError, match="symbolic link"):
        producer.build_bundle(source, audio, output)
    assert outside.is_file()
    assert not output.exists()
    assert not list(output.parent.glob(".output-*"))


def test_output_inside_source_is_rejected(fixture_bundle):
    producer, source, audio, _ = fixture_bundle
    with pytest.raises(ValueError, match="inside the source"):
        producer.build_bundle(source, audio, source / "output")
    assert not (source / "output").exists()


def test_unexpected_cli_files_are_not_published(fixture_bundle, monkeypatch):
    producer, source, audio, output = fixture_bundle
    monkeypatch.setattr(producer, "_create_calibration", lambda *args: ({}, {}))

    def unexpected_output(command, **kwargs):
        staging = Path(command[-1])
        staging.mkdir()
        (staging / "unexpected.npz").write_bytes(b"unexpected data")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(producer.subprocess, "run", unexpected_output)
    with pytest.raises(ValueError, match="CLI output file set mismatch"):
        producer.build_bundle(source, audio, output)
    assert not output.exists()
    assert not list(output.parent.glob(".output-*"))


def test_tampered_calibration_provenance_is_not_published(fixture_bundle, monkeypatch):
    producer, source, audio, output = fixture_bundle
    run = subprocess.run

    def tamper_cli(command, **kwargs):
        result = run(command, **kwargs)
        manifest_path = Path(command[-1]) / "export.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["quantization"]["calibration"]["embedding"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return result

    monkeypatch.setattr(producer.subprocess, "run", tamper_cli)
    with pytest.raises(ValueError, match="CLI calibration provenance mismatch"):
        producer.build_bundle(source, audio, output)
    assert not output.exists()
    assert not list(output.parent.glob(".output-*"))
