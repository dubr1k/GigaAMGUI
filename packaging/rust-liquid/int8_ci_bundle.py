#!/usr/bin/env python3
"""Build an offline, proof-only INT8 variant of the pinned Community-1 bundle.

Usage: python packaging/rust-liquid/int8_ci_bundle.py --source DIR --output DIR

The source must contain the four exact assets from the public
community-1-fp32-v1 release. The output parent must exist, and the output
itself must not exist. Nothing is downloaded. Calibration uses only one
fixed, synthetic fbank/mask pair; it is not representative speech and does
not establish diarization quality, speed, Tract support, or release readiness.

CI dependencies: Python 3.11, numpy==2.2.6, onnx==1.19.1,
onnxruntime==1.23.2. The versions actually used are recorded in the manifest.
"""

import argparse
import hashlib
import importlib.util
import json
import platform
import tempfile
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnxruntime.quantization import CalibrationDataReader


# Independently pinned, not taken from the downloaded export.json.
PINNED_ASSETS = {
    "export.json": (2882, "99cf740b8093e4a4c7491fcfdd389887941a470ac651887694384940a2dea7ad"),
    "plda.json": (983502, "8cadc0a017fce6e9fc9f1802b40b7a2a367889ec6f6e01b31f7b5b5adeead17a"),
    "tract/segmentation.onnx": (5984909, "8e8ddc4b066e14bf6fdc0e97a93206326de6346f7b99df9f06a5f9903d3ae2b5"),
    "tract/embedding.onnx": (26553403, "26ce0859c0afd8084e60b3ccd9d9f0a1999785ac18b13e1abf51cca50caea5af"),
}
FIRST_CONV = "/resnet/conv1/Conv"

# Reuse the exporter's existing bundle validation and atomic publication rules.
_spec = importlib.util.spec_from_file_location(
    "community_export_quantize", Path(__file__).parent / "community-export" / "quantize.py"
)
if _spec is None or _spec.loader is None:
    raise ImportError("Cannot load Community-1 export validation helpers")
_support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_support)


class _SyntheticReader(CalibrationDataReader):
    def __init__(self, batch):
        self.batch = batch
        self.used = False

    def get_next(self):
        if self.used:
            return None
        self.used = True
        return self.batch

    def rewind(self):
        self.used = False


def _synthetic_batch():
    fbank = np.random.default_rng(0).standard_normal((1, 998, 80), dtype=np.float32)
    mask = np.ones((1, 589), dtype=np.float32)
    mask[:, ::3] = 0.0
    batch = {"fbank": fbank, "mask": mask}
    for name, shape in (("fbank", (1, 998, 80)), ("mask", (1, 589))):
        array = batch[name]
        if array.dtype != np.float32 or array.shape != shape or not np.isfinite(array).all():
            raise ValueError(f"invalid synthetic calibration input: {name}")
    if np.any(mask < 0) or np.any(mask > 1) or not np.any(mask > 0):
        raise ValueError("invalid synthetic calibration mask")
    return batch


def _array_record(array):
    # Canonical little-endian bytes make the synthetic inputs independently identifiable.
    raw = array.astype("<f4", copy=False).tobytes(order="C")
    return {"dtype": "float32", "shape": list(array.shape), "sha256": hashlib.sha256(raw).hexdigest()}


def build_bundle(source, output):
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("source must be a directory")
    output = Path(output).expanduser()
    if output.name in ("", ".", ".."):
        raise ValueError("output must name a new directory")
    output = output.parent.resolve(strict=True) / output.name
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists: {output}")
    if output.is_relative_to(source):
        raise ValueError("output must not be inside the source directory")

    # Copy and verify the pinned inputs before parsing or running any of them.
    # Work is private to this invocation and publication is a single no-replace rename.
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        work = Path(temporary)
        fp32 = work / "fp32"
        fp32.mkdir()
        source_records = {}
        for name, (size, expected) in PINNED_ASSETS.items():
            path = source / name
            if not path.is_file() or path.stat().st_size != size:
                raise ValueError(f"source asset missing or size mismatch: {name}")
            digest = _support._copy_asset(source, fp32, name, expected=expected)
            if (fp32 / name).stat().st_size != size:
                raise ValueError(f"source asset size changed while copying: {name}")
            source_records[name] = {"size": size, "sha256": digest}

        _, manifest = _support._read_json(fp32 / "export.json")
        _support._validate_manifest(manifest)
        for name in _support.RUNTIME_FILES:
            if manifest["runtime_sha256"][name] != source_records[name]["sha256"]:
                raise ValueError(f"source manifest runtime hash mismatch: {name}")
        _support._validate_plda(fp32 / "plda.json")

        # The source and generated graph must both accept the native fixed shapes
        # and produce finite float32 outputs. Segmentation remains byte-identical.
        batch = _synthetic_batch()
        embedding = _support._session(fp32 / "tract/embedding.onnx", "embedding")
        _support._run_and_validate(embedding, "embedding", "FP32 embedding", batch)
        segmentation = _support._session(fp32 / "tract/segmentation.onnx", "segmentation")
        waveforms = {"waveforms": np.zeros((1, 1, 160000), dtype=np.float32)}
        _support._run_and_validate(segmentation, "segmentation", "FP32 segmentation", waveforms)

        staging = work / "bundle"
        staging.mkdir()
        for name in ("plda.json", "tract/segmentation.onnx"):
            _support._copy_asset(fp32, staging, name, expected=source_records[name]["sha256"])
        target = staging / "tract/embedding.onnx"
        qconv_count = _support._quantize_embedding_mixed(
            fp32 / "tract/embedding.onnx", target, _SyntheticReader(batch)
        )
        quantized = _support._session(target, "embedding")
        _support._run_and_validate(quantized, "embedding", "INT8 embedding", batch)

        runtime_hashes = {name: _support._sha256(staging / name) for name in _support.RUNTIME_FILES}
        manifest["runtime_sha256"] = runtime_hashes
        # Exporter's FP32 graph check cannot describe the altered embedding.
        manifest.pop("graph_check", None)
        manifest["segmentation"]["input"] = {"waveforms": [1, 1, 160000]}
        manifest["segmentation"]["output"] = {"scores": [1, 589, 7]}
        manifest["embedding"]["inputs"] = {"fbank": [1, 998, 80], "mask": [1, 589]}
        manifest["embedding"]["output"] = {"embedding": [1, 256]}
        manifest["quantization"] = {
            "proof_only": True,
            "format": "QOperator",
            "operators": ["Conv"],
            "per_channel": True,
            "activation_type": "QUInt8",
            "weight_type": "QInt8",
            "excluded_nodes": [FIRST_CONV],
            "calibration": "one deterministic synthetic fbank and mask; not representative speech",
        }
        manifest["limitations"] = (
            "Proof only: synthetic calibration and ONNX Runtime CPU shape/finiteness checks. "
            "Diarization quality, speed, Tract execution and release readiness are not established."
        )
        (staging / "export.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
        output_hashes = {name: _support._sha256(staging / name) for name in PINNED_ASSETS}
        proof = {
            "schema_version": 1,
            "proof_only": True,
            "source_release": "community-1-fp32-v1",
            "source_files": source_records,
            "output_files_sha256": output_hashes,
            "quantization": {**manifest["quantization"], "qlinearconv_count": qconv_count},
            "calibration": {
                "synthetic": True,
                "fbank_generator": "numpy default_rng(0).standard_normal(dtype=float32)",
                "mask_generator": "ones with every third value zero starting at index 0",
                "inputs": {name: _array_record(value) for name, value in batch.items()},
            },
            "validation": {
                "provider": "CPUExecutionProvider",
                "fp32_and_int8_embedding_shape": [1, 256],
                "fp32_segmentation_shape": [1, 589, 7],
                "outputs_finite": True,
                "quality_speed_tract_and_release_established": False,
            },
            "versions": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "onnx": onnx.__version__,
                "onnxruntime": ort.__version__,
            },
        }
        (staging / "proof-manifest.json").write_text(
            json.dumps(proof, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
        _support._rename_noreplace(staging, output)
    return output_hashes["tract/embedding.onnx"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="directory with the four pinned FP32 assets")
    parser.add_argument("--output", type=Path, required=True, help="new output directory (parent must exist)")
    args = parser.parse_args()
    try:
        digest = build_bundle(args.source, args.output)
    except Exception as error:
        parser.exit(2, f"proof bundle failed: {error}\n")
    print(f"embedding_sha256={digest}")
    print("Proof only: no diarization quality, speed, Tract execution, or release claim.")


if __name__ == "__main__":
    main()
