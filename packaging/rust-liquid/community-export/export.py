"""Export the two Community-1 neural graphs from a trusted local snapshot.

Build-only example, after installing requirements.txt in a separate Python 3.11
virtual environment and obtaining an authorized, complete snapshot:

    python export.py --snapshot /path/to/snapshot --output /new/output/directory

No model is downloaded. Hugging Face offline mode is forced and pyannote
telemetry is disabled before importing pyannote. Model.from_pretrained loads
pickle-based Lightning checkpoints with weights_only=False: ONLY use a trusted
snapshot. Offline environment flags are not a sandbox for malicious checkpoints.

The segmentation graph returns the model's unmodified powerset scores. The
embedding graph starts *after* pyannote's Kaldi fbank frontend and retains the
two-dimensional speaker mask in its source ResNet statistics pooling. It does
not export the source's optional unmasked or three-dimensional mask interfaces.
Consumers must reproduce the frontend, including 2**15 waveform scaling, Kaldi
options and the centering described by the recorded source model hyperparameters.
PLDA files are copied, not converted. This is not a complete diarization pipeline.

The model card is preserved for attribution; model access and redistribution
terms must be checked independently. ONNX/source checks cover only the generated
neural graphs and specific inputs, not diarization quality, licensing, or release
readiness. The application does not use these files yet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
from importlib import metadata
from pathlib import Path


PINNED_PACKAGES = {
    "numpy": "2.2.6",
    "onnx": "1.19.1",
    "onnxsim": "0.8.1",
    "onnxruntime": "1.23.2",
    "pyannote-audio": "4.0.7",
    "torch": "2.8.0",
    "torchaudio": "2.8.0",
    "torchcodec": "0.7.0",
}
SOURCE_FILES = (
    "README.md",
    "config.yaml",
    "segmentation/pytorch_model.bin",
    "embedding/pytorch_model.bin",
    "plda/plda.npz",
    "plda/xvec_transform.npz",
)
LFS_PREFIX = b"version https://git-lfs.github.com/spec/v1"
SAMPLES = 160_000
SEGMENTATION_FRAMES = 589
FBANK_FRAMES = 998
MASK_FRAMES = 589


def _source_files(snapshot: Path) -> dict[str, Path]:
    snapshot = Path(snapshot).resolve(strict=True)
    if not snapshot.is_dir():
        raise ValueError("snapshot must be a local directory")
    files = {}
    for name in SOURCE_FILES:
        path = snapshot / name
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"missing or empty snapshot file: {name}")
        with path.open("rb") as source:
            if source.read(len(LFS_PREFIX)) == LFS_PREFIX:
                raise ValueError(f"snapshot contains an unfetched Git LFS pointer: {name}")
        files[name] = path
    return files


def _destination(output: Path, snapshot: Path) -> Path:
    output = Path(output).expanduser()
    if output.name in ("", ".", ".."):
        raise ValueError("output must name a new directory")
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("output parent must be an existing directory")
    destination = parent / output.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"output already exists: {destination}")
    if destination.is_relative_to(snapshot.resolve(strict=True)):
        raise ValueError("output must not be inside the source snapshot")
    return destination


def _offline_environment() -> None:
    # Set before importing any package that caches these values at import time.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"


def _versions() -> dict[str, str]:
    found = {}
    for package, expected in PINNED_PACKAGES.items():
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError as error:
            raise RuntimeError(f"install the pinned build dependency {package}=={expected}") from error
        # CPU/CUDA PyTorch wheels may carry a local version suffix.
        if actual.split("+", 1)[0] != expected:
            raise RuntimeError(f"{package} must be {expected}; found {actual}")
        found[package] = actual
    return found


def _validate_plda(files: dict[str, Path]) -> None:
    import numpy as np

    for name, keys in (
        ("plda/plda.npz", ("mu", "tr", "psi")),
        ("plda/xvec_transform.npz", ("mean1", "mean2", "lda")),
    ):
        try:
            with np.load(files[name], allow_pickle=False) as archive:
                for key in keys:
                    array = archive[key]
                    if array.size == 0 or array.dtype.kind not in "fiu" or not np.isfinite(array).all():
                        raise ValueError(f"invalid {key} array")
        except (OSError, ValueError, KeyError, EOFError) as error:
            raise ValueError(f"invalid PLDA archive {name}: {error}") from error

def _convert_plda(files: dict[str, Path]) -> dict:
    """Convert pyannote's PLDA setup into the numeric VBx runtime contract."""
    import numpy as np
    from scipy.linalg import eigh

    with np.load(files["plda/plda.npz"], allow_pickle=False) as plda, \
         np.load(files["plda/xvec_transform.npz"], allow_pickle=False) as xvec:
        mu, original_tr, psi = (np.asarray(plda[k], dtype=np.float64) for k in ("mu", "tr", "psi"))
        mean1, mean2, lda = (np.asarray(xvec[k], dtype=np.float64) for k in ("mean1", "mean2", "lda"))
    n = mu.size
    if (mu.ndim != 1 or mean1.ndim != 1 or mean2.shape != (n,)
            or lda.shape != (mean1.size, n) or original_tr.shape != (n, n)
            or psi.shape != (n,) or n == 0 or np.any(psi <= 0)):
        raise ValueError("incompatible PLDA dimensions or non-positive variances")
    within = np.linalg.inv(original_tr.T @ original_tr)
    between = np.linalg.inv((original_tr.T / psi) @ original_tr)
    eigenvalues, eigenvectors = eigh(between, within)
    transformed = eigenvectors.T[::-1][:128]
    phi = eigenvalues[::-1][:128]
    if not all(np.isfinite(value).all() for value in (within, between, transformed, phi)):
        raise ValueError("PLDA conversion produced non-finite values")
    return {"mean1": mean1.tolist(), "mean2": mean2.tolist(), "lda": lda.tolist(),
            "mu": mu.tolist(), "tr": transformed.tolist(), "phi": phi.tolist()}


def _runtime_metadata(segmentation, embedding) -> dict:
    import torch

    receptive = segmentation.receptive_field
    with torch.inference_mode():
        lower, upper = 2, round(0.5 * embedding.hparams.sample_rate)
        while lower + 1 < upper:
            middle = (lower + upper) // 2
            try:
                embedding(torch.randn(1, 1, middle))
                upper = middle
            except Exception:
                lower = middle
    return {"receptive_field_start": float(receptive.start),
            "receptive_field_step": float(receptive.step),
            "receptive_field_duration": float(receptive.duration),
            "embedding_min_num_samples": upper}


def _load_models(files: dict[str, Path]):
    from pyannote.audio import Model

    # Explicit existing filenames take pyannote's local-file branch, not the hub.
    segmentation = Model.from_pretrained(files["segmentation/pytorch_model.bin"])
    embedding = Model.from_pretrained(files["embedding/pytorch_model.bin"])
    if segmentation is None or embedding is None:
        raise ValueError("could not load both local model checkpoints")
    return segmentation.cpu().eval(), embedding.cpu().eval()


def _check_models(segmentation, embedding) -> dict:
    import torch

    if type(segmentation).__name__ != "PyanNet":
        raise ValueError("segmentation checkpoint is not a PyanNet")
    if type(embedding).__name__ != "WeSpeakerResNet34":
        raise ValueError("embedding checkpoint is not a WeSpeakerResNet34")
    for name, model in (("segmentation", segmentation), ("embedding", embedding)):
        if model.hparams.sample_rate != 16_000 or model.hparams.num_channels != 1:
            raise ValueError(f"{name} checkpoint must expect 16 kHz mono audio")
        if any(p.is_floating_point() and p.dtype != torch.float32 for p in model.parameters()):
            raise ValueError(f"{name} checkpoint must use float32 parameters")
    specifications = segmentation.specifications
    if (
        not specifications.powerset
        or specifications.num_powerset_classes != 7
        or len(specifications.classes) != 3
        or specifications.powerset_max_classes != 2
    ):
        raise ValueError("segmentation must use the three-speaker seven-class powerset")
    keys = (
        "sample_rate", "num_channels", "num_mel_bins", "frame_length", "frame_shift",
        "round_to_power_of_two", "snip_edges", "dither", "window_type", "use_energy",
        "fbank_centering_span",
    )
    parameters = {key: getattr(embedding.hparams, key) for key in keys}
    if parameters["num_mel_bins"] != 80:
        raise ValueError("embedding checkpoint must use 80 fbank bins")
    return parameters


def _export_models(segmentation, embedding, output: Path) -> dict:
    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch

    class MaskedResNet(torch.nn.Module):
        def __init__(self, resnet):
            super().__init__()
            self.resnet = resnet

        def forward(self, fbank, mask):
            return self.resnet(fbank, weights=mask)[1]

    generator = torch.Generator().manual_seed(0)
    waveforms = torch.randn((2, 1, SAMPLES), generator=generator, dtype=torch.float32) * 0.02
    mask = torch.ones((2, MASK_FRAMES), dtype=torch.float32)
    mask[:, ::3] = 0.0
    mask[1, ::4] = 0.0
    network = MaskedResNet(embedding.resnet).eval()

    with torch.no_grad():
        fbank = embedding.compute_fbank(waveforms)
        if tuple(fbank.shape) != (2, FBANK_FRAMES, 80) or fbank.dtype != torch.float32:
            raise ValueError(f"unexpected source fbank shape or dtype: {fbank.shape}, {fbank.dtype}")
        if not torch.isfinite(fbank).all():
            raise ValueError("source fbank contains non-finite values")

        torch.onnx.export(
            segmentation, (waveforms[:1],), str(output / "segmentation.onnx"),
            input_names=["waveforms"], output_names=["scores"],
            dynamic_axes={"waveforms": {0: "batch"}, "scores": {0: "batch"}},
            opset_version=17, dynamo=False,
        )
        torch.onnx.export(
            network, (fbank[:1], mask[:1]), str(output / "embedding.onnx"),
            input_names=["fbank", "mask"], output_names=["embedding"],
            dynamic_axes={
                "fbank": {0: "batch", 1: "fbank_frames"},
                "mask": {0: "batch", 1: "mask_frames"},
                "embedding": {0: "batch"},
            },
            opset_version=17, dynamo=False,
        )
        for name in ("segmentation.onnx", "embedding.onnx"):
            onnx.checker.check_model(str(output / name))

        seg_session = ort.InferenceSession(str(output / "segmentation.onnx"), providers=["CPUExecutionProvider"])
        emb_session = ort.InferenceSession(str(output / "embedding.onnx"), providers=["CPUExecutionProvider"])
        for session, inputs, result in (
            (seg_session, ["waveforms"], "scores"),
            (emb_session, ["fbank", "mask"], "embedding"),
        ):
            actual_inputs = session.get_inputs()
            if [item.name for item in actual_inputs] != inputs or any(item.type != "tensor(float)" for item in actual_inputs):
                raise ValueError("exported graph has unexpected inputs")
            if [item.name for item in session.get_outputs()] != [result]:
                raise ValueError("exported graph has unexpected outputs")

        errors = {}

        def compare(label, actual, reference, expected_shape):
            reference = reference.detach().cpu().numpy()
            if actual.shape != expected_shape or reference.shape != expected_shape:
                raise ValueError(f"{label} has an unexpected output shape")
            if not np.isfinite(actual).all() or not np.isfinite(reference).all():
                raise ValueError(f"{label} produced non-finite values")
            np.testing.assert_allclose(actual, reference, rtol=1e-4, atol=1e-4)
            errors[label] = float(np.max(np.abs(actual - reference)))

        for batch in (1, 2):
            wave = waveforms[:batch]
            seg_expected = segmentation(wave)
            seg_actual = seg_session.run(["scores"], {"waveforms": wave.numpy()})[0]
            compare(f"segmentation_batch_{batch}", seg_actual, seg_expected, (batch, SEGMENTATION_FRAMES, 7))

            weights = mask[:batch]
            emb_expected = embedding(wave, weights=weights)
            emb_actual = emb_session.run(
                ["embedding"], {"fbank": fbank[:batch].numpy(), "mask": weights.numpy()}
            )[0]
            compare(f"embedding_batch_{batch}", emb_actual, emb_expected, (batch, 256))

        short_fbank, short_mask = fbank[:, :499, :], mask[:, :317]
        short_expected = network(short_fbank, short_mask)
        short_actual = emb_session.run(
            ["embedding"], {"fbank": short_fbank.numpy(), "mask": short_mask.numpy()}
        )[0]
        compare("embedding_variable_frames", short_actual, short_expected, (2, 256))

        # Tract cannot consume the dynamic If/Resize graphs. Specialize the
        # documented 10-second inference shapes, and compare with PyTorch.
        from onnxsim import simplify
        tract = output / "tract"
        tract.mkdir()
        for name, shapes, feeds, expected in (
            ("segmentation", {"waveforms": [1, 1, SAMPLES]},
             {"waveforms": waveforms[:1].numpy()}, segmentation(waveforms[:1])),
            ("embedding", {"fbank": [1, FBANK_FRAMES, 80], "mask": [1, MASK_FRAMES]},
             {"fbank": fbank[:1].numpy(), "mask": mask[:1].numpy()},
             network(fbank[:1], mask[:1])),
        ):
            graph, valid = simplify(str(output / f"{name}.onnx"), overwrite_input_shapes=shapes)
            if not valid:
                raise ValueError(f"failed to simplify {name} graph")
            target = tract / f"{name}.onnx"
            onnx.save(graph, str(target))
            onnx.checker.check_model(str(target))
            actual = ort.InferenceSession(str(target), providers=["CPUExecutionProvider"]).run(None, feeds)[0]
            compare(f"tract_{name}", actual, expected, tuple(expected.shape))

    return {"rtol": 1e-4, "atol": 1e-4, "max_absolute_error": errors}


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export(snapshot: Path, output: Path) -> None:
    files = _source_files(snapshot)
    destination = _destination(output, Path(snapshot))
    _offline_environment()
    packages = _versions()
    _validate_plda(files)
    source_hashes = {name: _hash(path) for name, path in files.items()}

    # Exclusive creation prevents replacing a pre-existing user directory.
    destination.mkdir()
    try:
        segmentation, embedding = _load_models(files)
        fbank_parameters = _check_models(segmentation, embedding)
        validation = _export_models(segmentation, embedding, destination)
        runtime = _runtime_metadata(segmentation, embedding)
        (destination / "plda.json").write_text(
            json.dumps(_convert_plda(files), allow_nan=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        (destination / "plda").mkdir()
        for name in ("plda/plda.npz", "plda/xvec_transform.npz"):
            shutil.copyfile(files[name], destination / name)
            if _hash(destination / name) != source_hashes[name]:
                raise ValueError(f"source changed while copying {name}")
        shutil.copyfile(files["README.md"], destination / "SOURCE_MODEL_CARD.md")
        shutil.copyfile(files["config.yaml"], destination / "SOURCE_CONFIG.yaml")
        if any(_hash(path) != source_hashes[name] for name, path in files.items()):
            raise ValueError("snapshot changed while exporting")
        manifest = {
            "source": "user-supplied local snapshot; origin and revision not independently verified",
            "source_sha256": source_hashes,
            "runtime_sha256": {
                name: _hash(destination / name)
                for name in ("tract/segmentation.onnx", "tract/embedding.onnx", "plda.json")
            },
            "python": platform.python_version(),
            "packages": packages,
            "opset": 17,
            "segmentation": {
                "input": {"waveforms": ["batch", 1, SAMPLES]},
                "output": {"scores": ["batch", SEGMENTATION_FRAMES, 7]},
                "scores": "unmodified model powerset scores",
                "receptive_field_start": runtime["receptive_field_start"],
                "receptive_field_step": runtime["receptive_field_step"],
                "receptive_field_duration": runtime["receptive_field_duration"],
            },
            "embedding": {
                "inputs": {"fbank": ["batch", "fbank_frames", 80], "mask": ["batch", "mask_frames"]},
                "output": {"embedding": ["batch", 256]},
                "fbank_hyperparameters": fbank_parameters,
                "min_num_samples": runtime["embedding_min_num_samples"],
                "waveform_scale_before_fbank": 32768,
            },
            "graph_check": validation,
            "limitations": "Graph checks use synthetic inputs only; no pipeline parity or release approval is implied.",
        }
        (destination / "export.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except BaseException:
        shutil.rmtree(destination)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", required=True, type=Path, help="authorized local Community-1 snapshot")
    parser.add_argument("--output", required=True, type=Path, help="new output directory under an existing parent")
    args = parser.parse_args()
    export(args.snapshot, args.output)
    print(f"Exported graphs and PLDA to {args.output}; full pipeline parity is not established.")


if __name__ == "__main__":
    main()
