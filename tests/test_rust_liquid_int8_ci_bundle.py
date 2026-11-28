"""Synthetic conversion mechanics only; these are not model quality tests."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
TensorProto, helper, numpy_helper = onnx.TensorProto, onnx.helper, onnx.numpy_helper


SCRIPT = Path(__file__).resolve().parents[1] / "packaging/rust-liquid/int8_ci_bundle.py"


@pytest.fixture
def producer():
    spec = importlib.util.spec_from_file_location("int8_ci_bundle", SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load the proof bundle producer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_graph(path, nodes, inputs, output, initializers):
    graph = helper.make_graph(nodes, path.stem, inputs, [output], initializers)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(path))


def _segmentation(path):
    _save_graph(
        path,
        [
            helper.make_node("ReduceMean", ["waveforms"], ["mean"], axes=[1, 2], keepdims=1),
            helper.make_node("Add", ["mean", "scores_base"], ["scores"]),
        ],
        [helper.make_tensor_value_info("waveforms", TensorProto.FLOAT, [1, 1, 160000])],
        helper.make_tensor_value_info("scores", TensorProto.FLOAT, [1, 589, 7]),
        [numpy_helper.from_array(np.full((1, 589, 7), 0.1, dtype=np.float32), "scores_base")],
    )


def _embedding(path, *, input_frames=998, output_features=256, nonfinite=False):
    rng = np.random.default_rng(19)

    def weights(name, shape):
        return numpy_helper.from_array(rng.standard_normal(shape, dtype=np.float32) * 0.08, name)

    nodes = [
        helper.make_node("Transpose", ["fbank"], ["channels"], perm=[0, 2, 1]),
        helper.make_node("Conv", ["channels", "first_w", "first_b"], ["first"], name="/resnet/conv1/Conv"),
        helper.make_node("Relu", ["first"], ["first_relu"]),
        helper.make_node("Conv", ["first_relu", "second_w", "second_b"], ["second"], name="/resnet/layer1/Conv"),
        helper.make_node("Relu", ["second"], ["second_relu"]),
        helper.make_node("GlobalAveragePool", ["second_relu"], ["pooled"]),
        helper.make_node("Flatten", ["pooled"], ["flat"], axis=1),
        helper.make_node("Gemm", ["flat", "projection_w", "projection_b"], ["projected"], name="/resnet/projection/Gemm"),
        helper.make_node("ReduceMean", ["mask"], ["mask_mean"], axes=[1], keepdims=1),
        helper.make_node("Mul", ["projected", "mask_mean"], ["raw_embedding" if nonfinite else "embedding"]),
    ]
    initializers = [
        weights("first_w", (4, 80, 1)),
        numpy_helper.from_array(np.full(4, 0.1, dtype=np.float32), "first_b"),
        weights("second_w", (4, 4, 1)),
        numpy_helper.from_array(np.full(4, 0.05, dtype=np.float32), "second_b"),
        weights("projection_w", (4, output_features)),
        numpy_helper.from_array(np.full(output_features, 0.1, dtype=np.float32), "projection_b"),
    ]
    if nonfinite:
        nodes.append(helper.make_node("Div", ["raw_embedding", "zero"], ["embedding"]))
        initializers.append(numpy_helper.from_array(np.array(0, dtype=np.float32), "zero"))
    _save_graph(
        path,
        nodes,
        [
            helper.make_tensor_value_info("fbank", TensorProto.FLOAT, [1, input_frames, 80]),
            helper.make_tensor_value_info("mask", TensorProto.FLOAT, [1, 589]),
        ],
        helper.make_tensor_value_info("embedding", TensorProto.FLOAT, [1, output_features]),
        initializers,
    )


def _make_source(root, producer, *, input_frames=998, output_features=256, nonfinite=False):
    source = root / "source"
    (source / "tract").mkdir(parents=True)
    _segmentation(source / "tract/segmentation.onnx")
    _embedding(
        source / "tract/embedding.onnx", input_frames=input_frames,
        output_features=output_features, nonfinite=nonfinite,
    )
    plda = {
        "mean1": [0.0] * 256,
        "mean2": [0.0] * 128,
        "lda": [[0.0] * 128 for _ in range(256)],
        "mu": [0.0] * 128,
        "tr": [[0.0] * 128 for _ in range(128)],
        "phi": [1.0] * 128,
    }
    (source / "plda.json").write_text(json.dumps(plda), encoding="utf-8")
    manifest = {
        "source_sha256": producer._support.SOURCE_HASHES,
        "runtime_sha256": {name: _hash(source / name) for name in producer._support.RUNTIME_FILES},
        "segmentation": {
            "receptive_field_start": 0.0,
            "receptive_field_step": 0.016875,
            "receptive_field_duration": 0.0619375,
        },
        "embedding": {
            "min_num_samples": 400,
            "fbank_hyperparameters": {
                "sample_rate": 16000, "num_channels": 1, "num_mel_bins": 80,
                "frame_length": 25.0, "frame_shift": 10.0, "round_to_power_of_two": True,
                "snip_edges": True, "dither": 0.0, "window_type": "hamming",
                "use_energy": False, "fbank_centering_span": None,
            },
        },
        "graph_check": {"fp32_only": True},
        "limitations": "synthetic fixture",
    }
    (source / "export.json").write_text(json.dumps(manifest), encoding="utf-8")
    return source


def _pin_fixture(monkeypatch, producer, source):
    # Only the test process replaces the immutable public asset pins; the CLI
    # itself exposes no option for accepting an unpinned input model.
    monkeypatch.setattr(producer, "PINNED_ASSETS", {
        name: ((source / name).stat().st_size, _hash(source / name))
        for name in producer.PINNED_ASSETS
    })


@pytest.fixture
def fixture_bundle(tmp_path, monkeypatch, producer):
    source = _make_source(tmp_path, producer)
    _pin_fixture(monkeypatch, producer, source)
    return producer, source, tmp_path / "output"


def test_synthetic_reader_rewinds_the_fixed_calibration_batch(producer):
    batch = producer._synthetic_batch()
    expected_fbank = np.random.default_rng(0).standard_normal((1, 998, 80), dtype=np.float32)
    expected_mask = np.ones((1, 589), dtype=np.float32)
    expected_mask[:, ::3] = 0.0
    np.testing.assert_array_equal(batch["fbank"], expected_fbank)
    np.testing.assert_array_equal(batch["mask"], expected_mask)

    reader = producer._SyntheticReader(batch)
    assert reader.get_next() is batch
    assert reader.get_next() is None
    reader.rewind()
    assert reader.get_next() is batch
    assert reader.get_next() is None


def test_cli_refuses_an_unpinned_source(tmp_path, monkeypatch, producer, capsys):
    source = _make_source(tmp_path, producer)
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--source", str(source), "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        producer.main()
    assert error.value.code == 2
    assert "size mismatch" in capsys.readouterr().err
    assert not output.exists()
    assert not list(tmp_path.glob(".output-*"))


def test_cli_converts_synthetic_graph_and_records_integrity(fixture_bundle, monkeypatch, capsys):
    producer, source, output = fixture_bundle
    source_hashes = {name: _hash(source / name) for name in producer.PINNED_ASSETS}
    shared_quantize = producer._support._quantize_embedding_mixed
    quantization_calls = []

    def record_quantization(source_path, output_path, reader):
        assert isinstance(reader, producer._SyntheticReader)
        assert not reader.used
        assert source_path != source / "tract/embedding.onnx"
        assert _hash(source_path) == source_hashes["tract/embedding.onnx"]
        assert not output_path.exists()
        inputs = {name: producer._array_record(value) for name, value in reader.batch.items()}
        count = shared_quantize(source_path, output_path, reader)
        quantization_calls.append((inputs, count))
        return count

    monkeypatch.setattr(producer._support, "_quantize_embedding_mixed", record_quantization)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--source", str(source), "--output", str(output)])
    producer.main()

    manifest = json.loads((output / "export.json").read_text(encoding="utf-8"))
    proof = json.loads((output / "proof-manifest.json").read_text(encoding="utf-8"))
    output_hashes = {name: _hash(output / name) for name in producer.PINNED_ASSETS}
    assert proof["proof_only"] is True
    assert proof["calibration"]["synthetic"] is True
    assert proof["calibration"]["inputs"]["fbank"]["shape"] == [1, 998, 80]
    assert proof["calibration"]["inputs"]["mask"]["shape"] == [1, 589]
    assert {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()} == (
        set(producer.PINNED_ASSETS) | {"proof-manifest.json"}
    )
    assert proof["output_files_sha256"] == output_hashes
    assert {name: record["sha256"] for name, record in proof["source_files"].items()} == source_hashes
    assert {name: _hash(source / name) for name in producer.PINNED_ASSETS} == source_hashes
    assert manifest["runtime_sha256"] == {name: output_hashes[name] for name in producer._support.RUNTIME_FILES}
    assert manifest["quantization"] == {
        "proof_only": True,
        "format": "QOperator",
        "operators": ["Conv"],
        "per_channel": True,
        "activation_type": "QUInt8",
        "weight_type": "QInt8",
        "excluded_nodes": ["/resnet/conv1/Conv"],
        "calibration": "one deterministic synthetic fbank and mask; not representative speech",
    }
    assert "graph_check" not in manifest
    assert output_hashes["plda.json"] == source_hashes["plda.json"]
    assert output_hashes["tract/segmentation.onnx"] == source_hashes["tract/segmentation.onnx"]
    assert f"embedding_sha256={output_hashes['tract/embedding.onnx']}" in capsys.readouterr().out

    graph = onnx.load(str(output / "tract/embedding.onnx"))
    assert any(node.name == "/resnet/conv1/Conv" and node.op_type == "Conv" for node in graph.graph.node)
    assert sum(node.op_type == "QLinearConv" for node in graph.graph.node) == 1
    assert any(node.name == "/resnet/projection/Gemm" and node.op_type == "Gemm" for node in graph.graph.node)
    assert proof["quantization"] == {**manifest["quantization"], "qlinearconv_count": 1}
    assert quantization_calls == [(proof["calibration"]["inputs"], 1)]
    second = output.parent / "second"
    producer.build_bundle(source, second)
    second_proof = json.loads((second / "proof-manifest.json").read_text(encoding="utf-8"))
    assert second_proof["calibration"] == proof["calibration"]
    assert quantization_calls == [(proof["calibration"]["inputs"], 1)] * 2


@pytest.mark.parametrize("kind", ["file", "empty-directory", "nonempty-directory", "dangling-symlink"])
def test_never_overwrites_an_existing_output(fixture_bundle, kind):
    producer, source, output = fixture_bundle
    if kind == "file":
        output.write_bytes(b"keep")
    elif kind == "dangling-symlink":
        try:
            output.symlink_to(output.parent / "missing-target", target_is_directory=True)
        except OSError:
            pytest.skip("symbolic links are not available")
    else:
        output.mkdir()
        if kind == "nonempty-directory":
            (output / "keep").write_bytes(b"keep")

    with pytest.raises(FileExistsError, match="already exists"):
        producer.build_bundle(source, output)
    if kind == "file":
        assert output.read_bytes() == b"keep"
    elif kind == "empty-directory":
        assert list(output.iterdir()) == []
    elif kind == "nonempty-directory":
        assert (output / "keep").read_bytes() == b"keep"
    else:
        assert output.is_symlink()


@pytest.mark.parametrize("damage", ["missing", "size", "sha256", "manifest-hash"])
def test_rejects_damaged_source_and_publishes_nothing(fixture_bundle, monkeypatch, damage):
    producer, source, output = fixture_bundle
    path = source / "plda.json"
    if damage == "missing":
        path.unlink()
    elif damage == "size":
        path.write_bytes(path.read_bytes() + b" ")
    elif damage == "sha256":
        raw = path.read_bytes()
        path.write_bytes(b"X" + raw[1:])
    else:
        path = source / "export.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["runtime_sha256"]["plda.json"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        # Keep fixture pinning valid so the manifest cross-check is exercised.
        pins = dict(producer.PINNED_ASSETS)
        pins["export.json"] = (path.stat().st_size, _hash(path))
        monkeypatch.setattr(producer, "PINNED_ASSETS", pins)

    with pytest.raises(ValueError, match="(missing|size|hash)"):
        producer.build_bundle(source, output)
    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}-*"))


def test_rejects_even_a_matching_symlink_source(fixture_bundle):
    producer, source, output = fixture_bundle
    path = source / "plda.json"
    outside = output.parent / "elsewhere.json"
    path.rename(outside)
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are not available")
    with pytest.raises(ValueError, match="symbolic link"):
        producer.build_bundle(source, output)
    assert not output.exists()
    assert outside.is_file()


@pytest.mark.parametrize("fault", ["input-shape", "output-shape", "nonfinite-output"])
def test_rejects_wrong_embedding_contract(tmp_path, monkeypatch, producer, fault):
    options = {
        "input_frames": 997 if fault == "input-shape" else 998,
        "output_features": 128 if fault == "output-shape" else 256,
        "nonfinite": fault == "nonfinite-output",
    }
    source = _make_source(tmp_path, producer, **options)
    _pin_fixture(monkeypatch, producer, source)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="(shape|finite)"):
        producer.build_bundle(source, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".output-*"))


def test_rejects_embedding_missing_excluded_conv(fixture_bundle, monkeypatch):
    producer, source, output = fixture_bundle
    embedding = source / "tract/embedding.onnx"
    model = onnx.load(str(embedding))
    first_conv = next(node for node in model.graph.node if node.name == "/resnet/conv1/Conv")
    first_conv.name = "/renamed/conv1/Conv"
    onnx.save(model, str(embedding))

    manifest_path = source / "export.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime_sha256"]["tract/embedding.onnx"] = _hash(embedding)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _pin_fixture(monkeypatch, producer, source)

    with pytest.raises(ValueError):
        producer.build_bundle(source, output)
    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}-*"))


def test_shared_quantizer_failure_does_not_publish_partial_output(fixture_bundle, monkeypatch):
    producer, source, output = fixture_bundle

    def reject_graph(source_path, output_path, calibration_reader):
        output_path.write_bytes(b"partial")
        raise ValueError("shared quantizer rejected graph")

    monkeypatch.setattr(producer._support, "_quantize_embedding_mixed", reject_graph)
    with pytest.raises(ValueError, match="shared quantizer rejected graph"):
        producer.build_bundle(source, output)
    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}-*"))


def test_output_inside_source_is_rejected(fixture_bundle):
    producer, source, _ = fixture_bundle
    with pytest.raises(ValueError, match="inside the source"):
        producer.build_bundle(source, source / "output")
    assert not (source / "output").exists()


def test_atomic_publication_does_not_replace_a_racing_directory(fixture_bundle, monkeypatch):
    producer, source, output = fixture_bundle
    rename = producer._support._rename_noreplace

    def race(staging, destination):
        destination.mkdir()
        (destination / "keep").write_bytes(b"other writer")
        rename(staging, destination)

    monkeypatch.setattr(producer._support, "_rename_noreplace", race)
    with pytest.raises(FileExistsError):
        producer.build_bundle(source, output)
    assert (output / "keep").read_bytes() == b"other writer"
    assert sorted(path.name for path in output.iterdir()) == ["keep"]
    assert not list(output.parent.glob(f".{output.name}-*"))
