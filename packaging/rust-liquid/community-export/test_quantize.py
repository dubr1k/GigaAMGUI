"""Synthetic graphs test conversion mechanics, not Community-1 quality."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import onnx
import onnxruntime as ort
import quantize
from onnx import TensorProto, helper, numpy_helper


SCRIPT = Path(__file__).with_name("quantize.py")


def _initializer(name, value):
    return numpy_helper.from_array(np.asarray(value), name=name)


def _save_graph(path, nodes, inputs, output, initializers):
    graph = helper.make_graph(nodes, path.stem, inputs, [output], initializers)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    # Opset 13 only needs IR 7; IR 8 also works with older supported ORT versions.
    model.ir_version = 8
    onnx.save(model, str(path))


def _segmentation(path, *, nonfinite=False, wrong_shape=False, unsupported=False, wrong_output=False):
    waveforms = helper.make_tensor_value_info("waveforms", TensorProto.FLOAT, [1, 1, 160000])
    name = "wrong_scores" if wrong_output else "scores"
    frames = 588 if wrong_shape else 589
    output = helper.make_tensor_value_info(name, TensorProto.FLOAT, [1, frames, 7])
    if unsupported:
        nodes = [
            helper.make_node("Slice", ["waveforms", "starts", "ends", "axes"], ["sliced"]),
            helper.make_node("Reshape", ["sliced", "shape"], [name]),
        ]
        initializers = [
            _initializer("starts", np.array([0], dtype=np.int64)),
            _initializer("ends", np.array([589 * 7], dtype=np.int64)),
            _initializer("axes", np.array([2], dtype=np.int64)),
            _initializer("shape", np.array([-1, 589, 7], dtype=np.int64)),
        ]
    else:
        result = "raw_scores" if nonfinite else name
        nodes = [
            helper.make_node("Slice", ["waveforms", "starts", "ends", "axes"], ["sliced"]),
            helper.make_node("Transpose", ["sliced"], ["frames"], perm=[0, 2, 1]),
            helper.make_node("MatMul", ["frames", "weights"], [result]),
        ]
        initializers = [
            _initializer("starts", np.array([0], dtype=np.int64)),
            _initializer("ends", np.array([frames], dtype=np.int64)),
            _initializer("axes", np.array([2], dtype=np.int64)),
            _initializer("weights", np.linspace(-0.7, 0.7, 7, dtype=np.float32).reshape(1, 7)),
        ]
        if nonfinite:
            nodes.append(helper.make_node("Div", [result, "zero"], [name]))
            initializers.append(_initializer("zero", np.array(0, dtype=np.float32)))
    _save_graph(path, nodes, [waveforms], output, initializers)


def _embedding(path, *, nonfinite=False, wrong_shape=False, wrong_mask_name=False):
    mask_name = "unexpected_mask" if wrong_mask_name else "mask"
    fbank = helper.make_tensor_value_info("fbank", TensorProto.FLOAT, [1, 998, 80])
    mask = helper.make_tensor_value_info(mask_name, TensorProto.FLOAT, [1, 589])
    features = 128 if wrong_shape else 256
    output = helper.make_tensor_value_info("embedding", TensorProto.FLOAT, [1, features])
    result = "raw_embedding" if nonfinite else "embedding"
    nodes = [
        # Deliberately permit different frame counts, as the real mask path does.
        helper.make_node("ReduceMean", [mask_name], ["mean_mask"], axes=[1], keepdims=1),
        helper.make_node("Unsqueeze", ["mean_mask", "unsqueeze_axes"], ["mask_scale"]),
        helper.make_node("Mul", ["fbank", "mask_scale"], ["masked_fbank"]),
        helper.make_node("MatMul", ["masked_fbank", "weights"], ["projected"]),
        helper.make_node("ReduceMean", ["projected"], [result], axes=[1], keepdims=0),
    ]
    initializers = [
        _initializer("unsqueeze_axes", np.array([2], dtype=np.int64)),
        _initializer("weights", np.linspace(0.002, 0.01, 80 * features, dtype=np.float32).reshape(80, features)),
    ]
    if nonfinite:
        nodes.append(helper.make_node("Div", [result, "zero"], ["embedding"]))
        initializers.append(_initializer("zero", np.array(0, dtype=np.float32)))
    _save_graph(path, nodes, [fbank, mask], output, initializers)


class QuantizeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.models = self.root / "export"
        self.calibration = self.root / "calibration"
        self.output = self.root / "int8"
        (self.models / "tract").mkdir(parents=True)
        (self.calibration / "segmentation").mkdir(parents=True)
        (self.calibration / "embedding").mkdir()
        _segmentation(self.models / "tract" / "segmentation.onnx")
        _embedding(self.models / "tract" / "embedding.onnx")
        for kind in ("segmentation", "embedding"):
            shutil.copyfile(self.models / "tract" / f"{kind}.onnx", self.models / f"{kind}.onnx")
        plda = {
            "mean1": [0.0] * 256, "mean2": [0.0] * 128,
            "lda": [[0.0] * 128 for _ in range(256)], "mu": [0.0] * 128,
            "tr": [[0.0] * 128 for _ in range(128)], "phi": [1.0] * 128,
        }
        (self.models / "plda.json").write_text(json.dumps(plda), encoding="utf-8")
        (self.models / "SOURCE_MODEL_CARD.md").write_text("synthetic model card", encoding="utf-8")
        (self.models / "SOURCE_CONFIG.yaml").write_text("synthetic: true\n", encoding="utf-8")
        manifest = {
            "source": "synthetic fixture; not Community-1 weights",
            "source_sha256": {
                **quantize.SOURCE_HASHES,
                "README.md": self._hash(self.models / "SOURCE_MODEL_CARD.md"),
                "config.yaml": self._hash(self.models / "SOURCE_CONFIG.yaml"),
            },
            "runtime_sha256": {
                name: self._hash(self.models / name) for name in quantize.RUNTIME_FILES
            },
            "opset": 13,
            "segmentation": {
                "receptive_field_start": 0.0, "receptive_field_step": 0.016875,
                "receptive_field_duration": 0.0619375, "scores": "unmodified",
            },
            "embedding": {
                "min_num_samples": 400,
                "fbank_hyperparameters": {
                    "sample_rate": 16000, "num_channels": 1, "num_mel_bins": 80,
                    "frame_length": 25, "frame_shift": 10, "round_to_power_of_two": True,
                    "snip_edges": True, "dither": 0.0, "window_type": "hamming",
                    "use_energy": False, "fbank_centering_span": None,
                },
            },
            "graph_check": {"synthetic_fp32_only": True},
            "limitations": "Synthetic FP32 fixture.",
        }
        self._write_manifest(manifest)
        self.waveforms = np.linspace(-1, 1, 160000, dtype=np.float32).reshape(1, 1, 160000)
        self.fbank = np.linspace(0.1, 1, 998 * 80, dtype=np.float32).reshape(1, 998, 80)
        self.mask = np.linspace(0, 1, 589, dtype=np.float32).reshape(1, 589)
        self._save_batches()

    @staticmethod
    def _hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_manifest(self, manifest):
        (self.models / "export.json").write_text(json.dumps(manifest), encoding="utf-8")

    def _refresh_runtime_hashes(self):
        path = self.models / "export.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["runtime_sha256"] = {
            name: self._hash(self.models / name) for name in quantize.RUNTIME_FILES
        }
        self._write_manifest(manifest)

    def _save_batches(self, *, segmentation=None, embedding=None):
        if segmentation is None:
            segmentation = {"waveforms": self.waveforms}
        if embedding is None:
            embedding = {"fbank": self.fbank, "mask": self.mask}
        np.savez(self.calibration / "segmentation" / "batch.npz", **segmentation)
        np.savez(self.calibration / "embedding" / "batch.npz", **embedding)

    def _assert_fails_closed(self, message):
        with self.assertRaisesRegex((ValueError, OSError), message):
            quantize.quantize(self.models, self.calibration, self.output)
        self.assertFalse(self.output.exists())

    def test_cli_quantizes_both_graphs_and_preserves_mask_input(self):
        np.savez(self.calibration / "embedding" / "ones.npz", fbank=self.fbank, mask=np.ones_like(self.mask))
        source_neighbor = self.models / "segmentation-inferred.onnx"
        source_neighbor.write_bytes(b"preserve")
        original_manifest = (self.models / "export.json").read_bytes()
        original_files = {
            name: self._hash(self.models / name)
            for name in ("segmentation.onnx", "embedding.onnx", *quantize.RUNTIME_FILES)
        }
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT), "--model-dir", str(self.models),
                "--calibration-dir", str(self.calibration), "--output-dir", str(self.output),
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Tract execution, quality and speed are not established", result.stdout)
        self.assertEqual(source_neighbor.read_bytes(), b"preserve")
        self.assertEqual({p.name for p in self.output.iterdir()}, {"fp32", "tract", "plda.json", "export.json"})
        self.assertEqual((self.models / "export.json").read_bytes(), original_manifest)
        self.assertEqual((self.output / "fp32" / "export.json").read_bytes(), original_manifest)
        for name, digest in original_files.items():
            self.assertEqual(self._hash(self.models / name), digest)
            self.assertEqual(self._hash(self.output / "fp32" / name), digest)
        for name in ("SOURCE_MODEL_CARD.md", "SOURCE_CONFIG.yaml"):
            self.assertEqual((self.output / "fp32" / name).read_bytes(), (self.models / name).read_bytes())
        manifest = json.loads((self.output / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_sha256"], json.loads(original_manifest)["source_sha256"])
        self.assertNotIn("graph_check", manifest)
        self.assertFalse(manifest["quantization"]["tract_execution_verified"])
        self.assertIsNone(manifest["quantization"]["quality_threshold"])
        self.assertEqual(manifest["quantization"]["activation_type"], {
            "segmentation": "QUInt8", "embedding": "QInt8",
        })
        self.assertEqual(manifest["quantization"]["calibration"]["segmentation"][0]["sha256"],
                         self._hash(self.calibration / "segmentation" / "batch.npz"))
        self.assertEqual(len(manifest["quantization"]["calibration"]["embedding"]), 2)
        self.assertGreaterEqual(manifest["quantization"]["calibration_max_absolute_error"]["embedding"], 0)
        for name in quantize.RUNTIME_FILES:
            self.assertEqual(manifest["runtime_sha256"][name], self._hash(self.output / name))
        self.assertEqual(list(self.output.rglob("*.npz")), [])
        for kind in ("segmentation", "embedding"):
            graph = onnx.load(str(self.output / "tract" / f"{kind}.onnx")).graph
            self.assertTrue(any(item.data_type == TensorProto.INT8 and item.dims for item in graph.initializer))
            self.assertTrue(any(node.op_type == "QuantizeLinear" for node in graph.node))
            self.assertTrue(any(node.op_type == "DequantizeLinear" for node in graph.node))

        segmentation = ort.InferenceSession(
            str(self.output / "tract" / "segmentation.onnx"), providers=["CPUExecutionProvider"]
        )
        scores = segmentation.run(["scores"], {"waveforms": self.waveforms})[0]
        self.assertEqual(scores.shape, (1, 589, 7))
        self.assertTrue(np.isfinite(scores).all())
        embedding = ort.InferenceSession(
            str(self.output / "tract" / "embedding.onnx"), providers=["CPUExecutionProvider"]
        )
        masked = embedding.run(["embedding"], {"fbank": self.fbank, "mask": self.mask})[0]
        unmasked = embedding.run(["embedding"], {"fbank": self.fbank, "mask": np.ones_like(self.mask)})[0]
        self.assertEqual(masked.shape, (1, 256))
        self.assertTrue(np.isfinite(masked).all() and np.isfinite(unmasked).all())
        self.assertFalse(np.allclose(masked, unmasked))

    def test_rejects_missing_empty_nonfinite_or_invalid_arrays(self):
        bad_waveforms = self.waveforms.copy()
        bad_waveforms[0, 0, 0] = np.nan
        bad_mask = self.mask.copy()
        bad_mask[0, 0] = np.inf
        bad_fbank = self.fbank.copy()
        bad_fbank[0, 0, 0] = np.nan
        cases = [
            ({}, None, "expected arrays"),
            ({"waveforms": bad_waveforms}, None, "finite float32"),
            ({"waveforms": self.waveforms.astype(np.float64)}, None, "finite float32"),
            ({"waveforms": self.waveforms[:, :, :-1]}, None, "shape"),
            (None, {"fbank": self.fbank}, "expected arrays"),
            (None, {"fbank": self.fbank, "mask": self.mask, "extra": self.mask}, "expected arrays"),
            (None, {"fbank": self.fbank, "mask": bad_mask}, "finite float32"),
            (None, {"fbank": bad_fbank, "mask": self.mask}, "finite float32"),
            (None, {"fbank": self.fbank, "mask": np.array([[]], dtype=np.float32)}, "nonempty"),
            (None, {"fbank": self.fbank[:, :, :-1], "mask": self.mask}, "shape"),
            (None, {"fbank": self.fbank, "mask": np.zeros_like(self.mask)}, "positive weight"),
            (None, {"fbank": self.fbank, "mask": self.mask + 0.1}, r"mask values must be in \[0, 1\]"),
            (None, {"fbank": self.fbank, "mask": self.mask - 0.1}, r"mask values must be in \[0, 1\]"),
            (None, {"fbank": self.fbank, "mask": np.ones((2, 589), dtype=np.float32)}, "shape"),
            (None, {"fbank": self.fbank, "mask": np.array([["unsafe"]], dtype=object)}, "invalid calibration"),
        ]
        for segmentation, embedding, message in cases:
            with self.subTest(message=message, segmentation=segmentation is not None):
                self._save_batches(segmentation=segmentation, embedding=embedding)
                self._assert_fails_closed(message)

    def test_rejects_missing_dataset(self):
        (self.calibration / "embedding" / "batch.npz").unlink()
        self._assert_fails_closed("no calibration")

    def test_checks_every_calibration_batch(self):
        bad = self.mask.copy()
        bad[0, 0] = np.nan
        np.savez(self.calibration / "embedding" / "later.npz", fbank=self.fbank, mask=bad)
        self._assert_fails_closed("finite float32")

    def test_cli_rejects_invalid_calibration_without_output(self):
        bad = self.mask.copy()
        bad[0, 0] = np.nan
        self._save_batches(embedding={"fbank": self.fbank, "mask": bad})
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--model-dir",
                str(self.models),
                "--calibration-dir",
                str(self.calibration),
                "--output-dir",
                str(self.output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("mask", result.stderr)
        self.assertIn("finite", result.stderr)
        self.assertFalse(self.output.exists())

    def test_rejects_wrong_graph_names_and_output_shapes(self):
        for kind in ("segmentation", "embedding"):
            for defect in ("name", "shape"):
                with self.subTest(kind=kind, defect=defect):
                    _segmentation(self.models / "tract" / "segmentation.onnx")
                    _embedding(self.models / "tract" / "embedding.onnx")
                    if kind == "segmentation":
                        _segmentation(self.models / "tract" / "segmentation.onnx", wrong_output=defect == "name", wrong_shape=defect == "shape")
                    else:
                        _embedding(self.models / "tract" / "embedding.onnx", wrong_mask_name=defect == "name", wrong_shape=defect == "shape")
                    self._refresh_runtime_hashes()
                    self._assert_fails_closed("expected (input|output)|must be finite")

    def test_rejects_nonfinite_reference_outputs_for_either_graph(self):
        for kind in ("segmentation", "embedding"):
            with self.subTest(kind=kind):
                _segmentation(self.models / "tract" / "segmentation.onnx", nonfinite=kind == "segmentation")
                _embedding(self.models / "tract" / "embedding.onnx", nonfinite=kind == "embedding")
                self._refresh_runtime_hashes()
                self._assert_fails_closed("must be finite")

    def test_rejects_graph_without_quantizable_int8_weights(self):
        _segmentation(self.models / "tract" / "segmentation.onnx", unsupported=True)
        self._refresh_runtime_hashes()
        # Depending on ORT's version, calibration itself can reject a graph with
        # no quantizable tensors, or it can return an unchanged graph.
        with self.assertRaises((ValueError, RuntimeError)):
            quantize.quantize(self.models, self.calibration, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_nonfinite_quantized_output_without_publishing_either_graph(self):
        real_quantize = quantize.quantize_static

        for kind, output_name in (("segmentation", "scores"), ("embedding", "embedding")):
            with self.subTest(kind=kind):
                def poison_output(*args, **kwargs):
                    real_quantize(*args, **kwargs)
                    path = Path(kwargs["model_output"])
                    if path.name != f"{kind}.onnx":
                        return
                    model = onnx.load(str(path))
                    for node in model.graph.node:
                        for index, name in enumerate(node.output):
                            if name == output_name:
                                node.output[index] = "output_before_poison"
                    model.graph.initializer.append(_initializer("poison_zero", np.array(0, dtype=np.float32)))
                    model.graph.node.append(
                        helper.make_node("Div", ["output_before_poison", "poison_zero"], [output_name])
                    )
                    onnx.save(model, str(path))

                with mock.patch.object(quantize, "quantize_static", side_effect=poison_output):
                    self._assert_fails_closed("must be finite")

    def test_existing_output_is_not_replaced(self):
        self.output.mkdir()
        marker = self.output / "keep"
        marker.write_text("unchanged", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            quantize.quantize(self.models, self.calibration, self.output)
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_rejects_invalid_manifest_runtime_hash_and_provenance(self):
        original = json.loads((self.models / "export.json").read_text(encoding="utf-8"))
        cases = [
            ("source", lambda m: m["source_sha256"].__setitem__("embedding/pytorch_model.bin", "0" * 64),
             "pinned source"),
            ("missing hash", lambda m: m["runtime_sha256"].pop("plda.json"), "runtime hash"),
            ("bad hash", lambda m: m["runtime_sha256"].__setitem__("plda.json", "A" * 64), "runtime hash"),
            ("mismatched hash", lambda m: m["runtime_sha256"].__setitem__("plda.json", "0" * 64), "hash mismatch"),
            ("metadata", lambda m: m["embedding"]["fbank_hyperparameters"].__setitem__("sample_rate", 8000),
             "runtime metadata"),
            ("boolean type", lambda m: m["embedding"]["fbank_hyperparameters"].__setitem__("snip_edges", 1),
             "runtime metadata"),
            ("already quantized", lambda m: m.__setitem__("quantization", {}), "already quantized"),
        ]
        for label, mutate, message in cases:
            with self.subTest(label=label):
                manifest = json.loads(json.dumps(original))
                mutate(manifest)
                self._write_manifest(manifest)
                self._assert_fails_closed(message)
        self._write_manifest(original)
        (self.models / "SOURCE_MODEL_CARD.md").write_text("tampered", encoding="utf-8")
        self._assert_fails_closed("hash mismatch")

    def test_rejects_missing_invalid_or_duplicate_manifest(self):
        path = self.models / "export.json"
        original = path.read_bytes()
        for contents in (
            b'{"source":NaN}', b'{"source":1e999}', b'{"source":"\\ud800"}',
            b'{"source":1,"source":2}', b"[]", b"\xef\xbb\xbf{}", "{}".encode("utf-16"),
        ):
            with self.subTest(contents=contents):
                path.write_bytes(contents)
                self._assert_fails_closed("JSON")
        path.write_bytes(original)
        path.unlink()
        self._assert_fails_closed("export.json")

    def test_rejects_invalid_plda_even_when_hash_matches(self):
        path = self.models / "plda.json"
        plda = json.loads(path.read_text(encoding="utf-8"))
        plda["phi"][0] = -1.0
        path.write_text(json.dumps(plda), encoding="utf-8")
        self._refresh_runtime_hashes()
        self._assert_fails_closed("PLDA")

    def test_rejects_corrupt_preserved_fp32_graph(self):
        (self.models / "embedding.onnx").write_bytes(b"not an ONNX model")
        with self.assertRaises(Exception):
            quantize.quantize(self.models, self.calibration, self.output)
        self.assertFalse(self.output.exists())

    def test_preserves_and_checks_exported_plda_provenance(self):
        source_hashes = {}
        for name in ("plda/plda.npz", "plda/xvec_transform.npz"):
            path = self.models / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(f"synthetic {name}".encode())
            source_hashes[name] = self._hash(path)
        manifest = json.loads((self.models / "export.json").read_text(encoding="utf-8"))
        manifest["source_sha256"].update(source_hashes)
        self._write_manifest(manifest)
        with mock.patch.dict(quantize.SOURCE_HASHES, source_hashes):
            quantize.quantize(self.models, self.calibration, self.output)
        for name, digest in source_hashes.items():
            self.assertEqual(self._hash(self.output / "fp32" / name), digest)
        generated = json.loads((self.output / "export.json").read_text(encoding="utf-8"))
        for name, digest in source_hashes.items():
            self.assertEqual(generated["source_sha256"][name], digest)

    def test_rejects_runtime_input_order_and_shape(self):
        path = self.models / "tract" / "embedding.onnx"
        original = onnx.load(str(path))
        for defect in ("order", "shape"):
            with self.subTest(defect=defect):
                model = onnx.ModelProto()
                model.CopyFrom(original)
                if defect == "order":
                    inputs = [onnx.ValueInfoProto(), onnx.ValueInfoProto()]
                    inputs[0].CopyFrom(model.graph.input[1])
                    inputs[1].CopyFrom(model.graph.input[0])
                    del model.graph.input[:]
                    model.graph.input.extend(inputs)
                else:
                    model.graph.input[0].type.tensor_type.shape.dim[1].dim_value = 999
                onnx.save(model, str(path))
                self._refresh_runtime_hashes()
                self._assert_fails_closed("expected inputs|fixed native input shapes")

    def test_rejects_external_data_without_loading_it(self):
        path = self.models / "tract" / "segmentation.onnx"
        model = onnx.load(str(path))
        tensor = model.graph.initializer[-1]
        tensor.ClearField("raw_data")
        tensor.data_location = TensorProto.EXTERNAL
        entry = tensor.external_data.add()
        entry.key, entry.value = "location", "../outside.bin"
        onnx.save(model, str(path))
        self._refresh_runtime_hashes()
        self._assert_fails_closed("external ONNX tensor")

    def test_calibration_is_snapshotted_before_quantization(self):
        original_hash = self._hash(self.calibration / "segmentation" / "batch.npz")
        real_quantize = quantize.quantize_static
        changed = False

        def mutate_calibration(*args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                np.savez(self.calibration / "segmentation" / "batch.npz",
                         waveforms=np.full_like(self.waveforms, np.nan))
            return real_quantize(*args, **kwargs)

        with mock.patch.object(quantize, "quantize_static", side_effect=mutate_calibration):
            quantize.quantize(self.models, self.calibration, self.output)
        manifest = json.loads((self.output / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["quantization"]["calibration"]["segmentation"][0]["sha256"], original_hash)
        self.assertNotEqual(self._hash(self.calibration / "segmentation" / "batch.npz"), original_hash)

    def test_rejects_output_inside_source_or_calibration(self):
        for parent in (self.models, self.calibration):
            with self.subTest(parent=parent):
                output = parent / "must-not-appear"
                with self.assertRaisesRegex(ValueError, "output must not be inside"):
                    quantize.quantize(self.models, self.calibration, output)
                self.assertFalse(output.exists())

    def test_concurrent_empty_output_is_not_replaced(self):
        real_rename = quantize._rename_noreplace

        def race(source, destination):
            destination.mkdir()
            real_rename(source, destination)

        with mock.patch.object(quantize, "_rename_noreplace", side_effect=race):
            with self.assertRaises(FileExistsError):
                quantize.quantize(self.models, self.calibration, self.output)
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
