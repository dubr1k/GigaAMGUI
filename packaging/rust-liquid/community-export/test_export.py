"""Independent exporter tests. No gated snapshot or network is required."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


spec = importlib.util.spec_from_file_location("community_export", Path(__file__).with_name("export.py"))
if spec is None or spec.loader is None:
    raise RuntimeError("could not load the local export module")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

try:
    import numpy as np
except ImportError:
    np = None

try:
    import onnx  # noqa: F401
    import onnxruntime as ort
    import torch
    import torch.nn.functional as functional
except ImportError:
    ort = None
    torch = None
    functional = None


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / "snapshot"
        self.snapshot.mkdir()
        for name in exporter.SOURCE_FILES:
            path = self.snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("synthetic " + name).encode())
        self.output = self.root / "output"

    def valid_plda(self):
        np.savez(self.snapshot / "plda/plda.npz", mu=[0.0], tr=[[1.0]], psi=[1.0])
        np.savez(self.snapshot / "plda/xvec_transform.npz", mean1=[0.0], mean2=[0.0], lda=[[1.0]])

    @unittest.skipIf(np is None, "numpy is not installed")
    def test_plda_conversion_preserves_vbx_transform(self):
        self.valid_plda()
        converted = exporter._convert_plda(exporter._source_files(self.snapshot))
        self.assertEqual(converted["mean1"], [0.0])
        self.assertEqual(converted["phi"], [1.0])
        self.assertEqual(abs(converted["tr"][0][0]), 1.0)

    @unittest.skipIf(np is None, "numpy is not installed")
    def test_plda_conversion_rejects_nonpositive_variance(self):
        self.valid_plda()
        np.savez(self.snapshot / "plda/plda.npz", mu=[0.0], tr=[[1.0]], psi=[0.0])
        with self.assertRaisesRegex(ValueError, "non-positive"):
            exporter._convert_plda(exporter._source_files(self.snapshot))

    def test_missing_file_rejected_before_output_is_created(self):
        (self.snapshot / "embedding/pytorch_model.bin").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "embedding/pytorch_model.bin"):
            exporter.export(self.snapshot, self.output)
        self.assertFalse(self.output.exists())

    def test_empty_checkpoint_and_unfetched_lfs_pointer_rejected(self):
        checkpoint = self.snapshot / "segmentation/pytorch_model.bin"
        for content, error in ((b"", FileNotFoundError), (exporter.LFS_PREFIX + b"\n", ValueError)):
            with self.subTest(content=content):
                checkpoint.write_bytes(content)
                with self.assertRaises(error):
                    exporter.export(self.snapshot, self.output)
                self.assertFalse(self.output.exists())

    def test_existing_output_never_replaced(self):
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_text("user data", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            exporter.export(self.snapshot, self.output)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user data")

    def test_dangling_output_symlink_never_replaced(self):
        try:
            self.output.symlink_to(self.root / "missing", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable")
        with self.assertRaises(FileExistsError):
            exporter.export(self.snapshot, self.output)
        self.assertTrue(self.output.is_symlink())

    def test_output_inside_snapshot_rejected(self):
        nested = self.snapshot / "export"
        with self.assertRaisesRegex(ValueError, "inside the source snapshot"):
            exporter.export(self.snapshot, nested)
        self.assertFalse(nested.exists())

    @unittest.skipIf(np is None, "numpy is not installed")
    def test_bad_plda_is_rejected_before_output(self):
        self.valid_plda()
        np.savez(self.snapshot / "plda/plda.npz", mu=[0.0], tr=[[1.0]])
        with mock.patch.object(exporter, "_versions", return_value={}):
            with self.assertRaisesRegex(ValueError, "invalid PLDA archive"):
                exporter.export(self.snapshot, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(np is None, "numpy is not installed")
    def test_nonfinite_plda_is_rejected(self):
        self.valid_plda()
        np.savez(self.snapshot / "plda/plda.npz", mu=[float("nan")], tr=[[1.0]], psi=[1.0])
        with mock.patch.object(exporter, "_versions", return_value={}):
            with self.assertRaisesRegex(ValueError, "invalid PLDA archive"):
                exporter.export(self.snapshot, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(np is None, "numpy is not installed")
    def test_load_failure_cleans_output_and_forces_offline_before_loading(self):
        self.valid_plda()

        def fail_load(files):
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            self.assertEqual(os.environ["HF_DATASETS_OFFLINE"], "1")
            self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")
            self.assertEqual(os.environ["HF_HUB_DISABLE_TELEMETRY"], "1")
            self.assertEqual(os.environ["PYANNOTE_METRICS_ENABLED"], "0")
            self.assertTrue(files["segmentation/pytorch_model.bin"].is_absolute())
            raise RuntimeError("synthetic load failure")

        with mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "0", "PYANNOTE_METRICS_ENABLED": "1"}):
            with mock.patch.object(exporter, "_versions", return_value={}):
                with mock.patch.object(exporter, "_load_models", side_effect=fail_load):
                    with self.assertRaisesRegex(RuntimeError, "synthetic load failure"):
                        exporter.export(self.snapshot, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(np is None, "numpy is not installed")
    def test_graph_export_failure_removes_partial_files(self):
        self.valid_plda()

        def fail_export(segmentation, embedding, destination):
            (destination / "segmentation.onnx").write_bytes(b"partial")
            raise RuntimeError("synthetic export failure")

        with mock.patch.object(exporter, "_versions", return_value={}), \
             mock.patch.object(exporter, "_load_models", return_value=(object(), object())), \
             mock.patch.object(exporter, "_check_models", return_value={}), \
             mock.patch.object(exporter, "_export_models", side_effect=fail_export):
            with self.assertRaisesRegex(RuntimeError, "synthetic export failure"):
                exporter.export(self.snapshot, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(np is None, "numpy is not installed")
    def test_snapshot_change_during_export_removes_output(self):
        self.valid_plda()

        def modify_source(segmentation, embedding, destination):
            (self.snapshot / "config.yaml").write_text("changed", encoding="utf-8")
            return {}

        with mock.patch.object(exporter, "_versions", return_value={}), \
             mock.patch.object(exporter, "_load_models", return_value=(object(), object())), \
             mock.patch.object(exporter, "_check_models", return_value={}), \
             mock.patch.object(exporter, "_runtime_metadata", return_value={}), \
             mock.patch.object(exporter, "_export_models", side_effect=modify_source):
            with self.assertRaisesRegex(ValueError, "snapshot changed"):
                exporter.export(self.snapshot, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(np is None or torch is None or ort is None, "ONNX export dependencies are not installed")
    def test_synthetic_graphs_keep_raw_scores_and_weighted_embedding_interface(self):
        self.valid_plda()

        class PyanNet(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.hparams = SimpleNamespace(sample_rate=16_000, num_channels=1)
                self.specifications = SimpleNamespace(
                    powerset=True, num_powerset_classes=7, classes=["a", "b", "c"],
                    powerset_max_classes=2,
                )

            def forward(self, waveforms):
                scale = torch.arange(1, 8, dtype=waveforms.dtype, device=waveforms.device)
                return waveforms[:, 0, :589].unsqueeze(-1) * scale

        class ResNet(torch.nn.Module):
            def forward(self, fbank, weights):
                resized = functional.interpolate(
                    weights.unsqueeze(1), size=fbank.shape[1], mode="nearest"
                ).transpose(1, 2)
                pooled = (fbank * resized).sum(dim=1) / resized.sum(dim=1).clamp(min=1e-6)
                result = pooled.repeat(1, 4)[:, :256]
                return torch.zeros_like(result), result

        class WeSpeakerResNet34(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.resnet = ResNet()
                self.hparams = SimpleNamespace(
                    sample_rate=16_000, num_channels=1, num_mel_bins=80,
                    frame_length=25.0, frame_shift=10.0, round_to_power_of_two=True,
                    snip_edges=True, dither=0.0, window_type="hamming", use_energy=False,
                    fbank_centering_span=None,
                )

            def compute_fbank(self, waveforms):
                bins = torch.arange(1, 81, dtype=waveforms.dtype, device=waveforms.device)
                return waveforms[:, 0, :998].unsqueeze(-1) * bins

            def forward(self, waveforms, weights):
                return self.resnet(self.compute_fbank(waveforms), weights=weights)[1]

        segmentation = PyanNet().eval()
        embedding = WeSpeakerResNet34().eval()
        with mock.patch.object(exporter, "_versions", return_value={}), \
             mock.patch.object(exporter, "_load_models", return_value=(segmentation, embedding)), \
             mock.patch.object(exporter, "_runtime_metadata", return_value={
                 "receptive_field_start": 0.0, "receptive_field_step": 0.1,
                 "receptive_field_duration": 0.2, "embedding_min_num_samples": 1}):
            exporter.export(self.snapshot, self.output)

        for name in ("plda/plda.npz", "plda/xvec_transform.npz"):
            self.assertEqual((self.output / name).read_bytes(), (self.snapshot / name).read_bytes())
        self.assertEqual((self.output / "SOURCE_MODEL_CARD.md").read_bytes(), (self.snapshot / "README.md").read_bytes())
        manifest = json.loads((self.output / "export.json").read_text(encoding="utf-8"))
        self.assertIn("not independently verified", manifest["source"])
        self.assertIn("embedding_variable_frames", manifest["graph_check"]["max_absolute_error"])
        self.assertIn("tract_segmentation", manifest["graph_check"]["max_absolute_error"])
        self.assertIn("tract_embedding", manifest["graph_check"]["max_absolute_error"])
        self.assertEqual(json.loads((self.output / "plda.json").read_text())["phi"], [1.0])

        session = ort.InferenceSession(str(self.output / "embedding.onnx"), providers=["CPUExecutionProvider"])
        features = np.random.default_rng(4).normal(size=(2, 400, 80)).astype(np.float32)
        all_speech = np.ones((2, 317), dtype=np.float32)
        half_speech = all_speech.copy()
        half_speech[:, 158:] = 0
        whole = session.run(["embedding"], {"fbank": features, "mask": all_speech})[0]
        part = session.run(["embedding"], {"fbank": features, "mask": half_speech})[0]
        self.assertEqual(whole.shape, (2, 256))
        self.assertFalse(np.allclose(whole, part, rtol=1e-6, atol=1e-8))


if __name__ == "__main__":
    unittest.main()
