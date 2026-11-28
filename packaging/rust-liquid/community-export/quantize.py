"""Calibrate an exported Community-1 bundle into an offline mixed FP32/INT8 bundle.

Dependencies: Python 3.10+, numpy, onnx, and onnxruntime (CPU). No model or
calibration data is downloaded. Supply one or more .npz files per graph:

  CALIBRATION/segmentation/*.npz: waveforms, float32 [1, 1, 160000]
  CALIBRATION/embedding/*.npz: fbank, float32 [1, 998, 80];
                             mask, float32 [1, 589]

Each file is one fixed-shape native inference batch. Waveforms are mono, 16 kHz,
ten-second windows. Prepare fbank with the source model's frontend conventions,
including waveform scaling by 32768 and its checkpoint's centering. Masks must
be in [0, 1], with positive weight in every row. Use representative, legally
obtained calibration arrays; this tool does not generate them.

Example:
  python quantize.py --model-dir EXPORT --calibration-dir CALIBRATION --output-dir INT8

INT8 contains FP32 segmentation, a mixed-precision embedding graph and an fp32/
copy of the original export assets. The two root dynamic graphs, if supplied,
must both be present; the four-file public runtime export does not require them.
Only embedding Conv operators after the first Conv are calibrated and quantized,
with per-output-channel INT8 weights and UINT8 activations. The first Conv and
projection remain FP32. Segmentation batches are still required for validation.
Calibration arrays are not copied into the bundle; their hashes and shapes are
recorded in export.json. Validation
runs both graphs on the supplied arrays with ONNX Runtime CPU, not Tract. No
quality threshold is imposed and neither diarization quality, parity, speed nor
target-platform support is established. Evaluate those separately on independent
recordings.
"""

import argparse
import ctypes
import errno
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, numpy_helper
from onnxruntime.quantization import (
    CalibrationDataReader,
    QuantFormat,
    QuantType,
    quantize_static,
)


CONTRACTS = {
    "segmentation": (("waveforms",), "scores", ((1, 1, 160000),)),
    "embedding": (("fbank", "mask"), "embedding", ((1, 998, 80), (1, 589))),
}
RUNTIME_FILES = ("tract/segmentation.onnx", "tract/embedding.onnx", "plda.json")
FIRST_CONV = "/resnet/conv1/Conv"
SOURCE_HASHES = {
    "segmentation/pytorch_model.bin": "7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e",
    "embedding/pytorch_model.bin": "6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929",
    "plda/plda.npz": "9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255",
    "plda/xvec_transform.npz": "325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f",
}
PROVENANCE_FILES = {
    "SOURCE_MODEL_CARD.md": "README.md",
    "SOURCE_CONFIG.yaml": "config.yaml",
    "plda/plda.npz": "plda/plda.npz",
    "plda/xvec_transform.npz": "plda/xvec_transform.npz",
}


def _session(path, kind):
    _load_onnx(path)
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    inputs, output, shapes = CONTRACTS[kind]
    metadata = session.get_inputs()
    outputs = session.get_outputs()
    if [item.name for item in metadata] != list(inputs):
        raise ValueError(f"{path}: expected inputs in order {list(inputs)}")
    if any(tuple(item.shape or ()) != shape for item, shape in zip(metadata, shapes, strict=True)):
        raise ValueError(f"{path}: expected fixed native input shapes {shapes}")
    if len(outputs) != 1 or outputs[0].name != output:
        raise ValueError(f"{path}: expected output {output!r}")
    if any(item.type != "tensor(float)" for item in [*metadata, *outputs]):
        raise ValueError(f"{path}: inputs and output must be float32 tensors")
    return session


def _load_batch(path, kind, metadata):
    expected, _, _ = CONTRACTS[kind]
    try:
        with np.load(path, allow_pickle=False) as archive:
            if len(archive.files) != len(expected) or set(archive.files) != set(expected):
                raise ValueError(f"expected arrays {sorted(expected)}, got {archive.files}")
            arrays = {name: archive[name] for name in expected}
    except (OSError, ValueError, KeyError, EOFError) as error:
        raise ValueError(f"{path}: invalid calibration archive: {error}") from error

    symbols = {}
    for item in metadata:
        array = arrays[item.name]
        if array.dtype != np.float32 or array.size == 0 or not np.isfinite(array).all():
            raise ValueError(f"{path}: {item.name} must be a nonempty, finite float32 array")
        if item.shape is None or array.ndim != len(item.shape) or any(size <= 0 for size in array.shape):
            raise ValueError(f"{path}: {item.name} has an invalid rank or empty dimension")
        for actual, declared in zip(array.shape, item.shape, strict=True):
            if isinstance(declared, int) and actual != declared:
                raise ValueError(f"{path}: {item.name} shape {array.shape} does not match {item.shape}")
            if isinstance(declared, str) and declared:
                if declared in symbols and symbols[declared] != actual:
                    raise ValueError(f"{path}: inconsistent symbolic dimension {declared!r}")
                symbols[declared] = actual

    if kind == "segmentation":
        if arrays["waveforms"].ndim != 3 or arrays["waveforms"].shape[1:] != (1, 160000):
            raise ValueError(f"{path}: waveforms must have shape [batch, 1, 160000]")
    else:
        fbank, mask = arrays["fbank"], arrays["mask"]
        if fbank.ndim != 3 or fbank.shape[2] != 80 or mask.ndim != 2 or fbank.shape[0] != mask.shape[0]:
            raise ValueError(f"{path}: expected fbank [batch, frames, 80] and mask [batch, mask_frames]")
        if np.any(mask < 0) or np.any(mask > 1) or not np.all(np.any(mask > 0, axis=1)):
            raise ValueError(f"{path}: mask values must be in [0, 1] with positive weight in each row")
    return arrays


def _run_and_validate(session, kind, path, batch):
    _, output_name, _ = CONTRACTS[kind]
    result = session.run([output_name], batch)[0]
    batch_size = next(iter(batch.values())).shape[0]
    expected_shape = (batch_size, 589, 7) if kind == "segmentation" else (batch_size, 256)
    if (
        not isinstance(result, np.ndarray)
        or result.dtype != np.float32
        or result.shape != expected_shape
        or not np.isfinite(result).all()
    ):
        raise ValueError(f"{path}: {output_name} must be finite float32 with shape {expected_shape}")
    return result


class _NpzReader(CalibrationDataReader):
    def __init__(self, paths, kind, metadata):
        self.paths = iter(paths)
        self.kind = kind
        self.metadata = metadata

    def get_next(self):
        path = next(self.paths, None)
        return None if path is None else _load_batch(path, self.kind, self.metadata)


def _validate_quantized_graph(source, output):
    """Verify that the requested mixed per-channel Conv cutover actually occurred."""
    original = _load_onnx(source)
    graph = _load_onnx(output)
    original_convs = [node for node in original.graph.node if node.op_type == "Conv"]
    if sum(node.name == FIRST_CONV for node in original_convs) != 1:
        raise ValueError(f"source embedding must have exactly one {FIRST_CONV}")
    if len(original_convs) < 2:
        raise ValueError("source embedding has no other Conv to quantize")
    first = [node for node in graph.graph.node if node.name == FIRST_CONV and node.op_type == "Conv"]
    qconvs = [node for node in graph.graph.node if node.op_type == "QLinearConv"]
    if len(first) != 1 or len(qconvs) != len(original_convs) - 1:
        raise ValueError("embedding did not preserve the first Conv and quantize every other Conv")

    initializers = {item.name: item for item in graph.graph.initializer}
    first_weight = initializers.get(first[0].input[1])
    if first_weight is None or first_weight.data_type != TensorProto.FLOAT:
        raise ValueError("excluded first Conv does not have FP32 weights")
    for node in qconvs:
        if len(node.input) < 8:
            raise ValueError("invalid QLinearConv inputs")
        activation_zero_points = (initializers.get(node.input[2]), initializers.get(node.input[7]))
        if any(item is None or item.data_type != TensorProto.UINT8 for item in activation_zero_points):
            raise ValueError("QLinearConv activation zero points are not UINT8")
        weight = initializers.get(node.input[3])
        scale = initializers.get(node.input[4])
        zero_point = initializers.get(node.input[5])
        if weight is None or weight.data_type != TensorProto.INT8 or len(weight.dims) < 2:
            raise ValueError("QLinearConv weights are not constant INT8 tensors")
        channels = weight.dims[0]
        if (
            scale is None or scale.data_type != TensorProto.FLOAT
            or tuple(scale.dims) != (channels,)
            or zero_point is None or zero_point.data_type != TensorProto.INT8
            or tuple(zero_point.dims) != (channels,)
        ):
            raise ValueError("QLinearConv weights are not quantized per output channel")
        values = numpy_helper.to_array(scale)
        if not np.isfinite(values).all() or np.any(values <= 0):
            raise ValueError("QLinearConv has invalid per-channel scales")

    # Restricting quantization to Conv must also leave the projection FP32.
    def projection_ops(model):
        return Counter((node.name, node.op_type) for node in model.graph.node
                       if node.op_type in {"Gemm", "MatMul"})

    if not projection_ops(original) or projection_ops(original) != projection_ops(graph):
        raise ValueError("embedding FP32 projection operators changed")
    if any(node.op_type in {"QLinearMatMul", "MatMulInteger"} for node in graph.graph.node):
        raise ValueError("embedding contains a quantized projection")
    return len(qconvs)


def _quantize_embedding_mixed(source_path, output_path, calibration_reader):
    # A model object keeps ORT's inference scratch files out of the source export.
    quantize_static(
        model_input=_load_onnx(source_path),
        model_output=str(output_path),
        calibration_data_reader=calibration_reader,
        quant_format=QuantFormat.QOperator,
        activation_type=QuantType.QUInt8,
        weight_type=QuantType.QInt8,
        per_channel=True,
        op_types_to_quantize=["Conv"],
        nodes_to_exclude=[FIRST_CONV],
        calibration_providers=["CPUExecutionProvider"],
    )
    return _validate_quantized_graph(source_path, output_path)


def _reject_external_tensors(message, path):
    # Do not let an ONNX graph read files outside the self-contained bundle.
    if isinstance(message, TensorProto) and (
        message.data_location == TensorProto.EXTERNAL or message.external_data
    ):
        raise ValueError(f"{path}: external ONNX tensor data is not supported")
    for field, value in message.ListFields():
        if field.type == field.TYPE_MESSAGE:
            repeated = getattr(field, "is_repeated", None)
            if repeated is None:
                repeated = field.label == field.LABEL_REPEATED
            children = value if repeated else (value,)
            for child in children:
                _reject_external_tensors(child, path)


def _load_onnx(path):
    model = onnx.load(str(path), load_external_data=False)
    _reject_external_tensors(model, path)
    onnx.checker.check_model(model)
    return model


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"invalid JSON number: {value}")

    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
        # serde_json consumes UTF-8 and rejects non-finite numbers and lone surrogates.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return raw, value


def _valid_hash(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_manifest(manifest):
    if "quantization" in manifest:
        raise ValueError("source bundle is already quantized; use its fp32/ export")
    try:
        source, runtime = manifest["source_sha256"], manifest["runtime_sha256"]
        segmentation, embedding = manifest["segmentation"], manifest["embedding"]
        fbank = embedding["fbank_hyperparameters"]
        if not isinstance(source, dict) or not isinstance(runtime, dict):
            raise ValueError("invalid Community-1 manifest hashes")
        if any(source.get(name) != expected for name, expected in SOURCE_HASHES.items()):
            raise ValueError("Community-1 manifest does not identify the pinned source")
        if any(not _valid_hash(runtime.get(name)) for name in RUNTIME_FILES):
            raise ValueError("missing or invalid Community-1 runtime hash")

        def matches(actual, expected):
            if expected is None:
                return actual is None
            if isinstance(expected, bool):
                return type(actual) is bool and actual == expected
            if isinstance(expected, int):
                return type(actual) is int and actual == expected
            if isinstance(expected, float):
                return type(actual) in (int, float) and actual == expected
            return type(actual) is type(expected) and actual == expected

        for values, expected in (
            (segmentation, {
                "receptive_field_start": 0.0,
                "receptive_field_step": 0.016875,
                "receptive_field_duration": 0.0619375,
            }),
            (embedding, {"min_num_samples": 400}),
            (fbank, {
                "sample_rate": 16000, "num_channels": 1, "num_mel_bins": 80,
                "frame_length": 25.0, "frame_shift": 10.0, "round_to_power_of_two": True,
                "snip_edges": True, "dither": 0.0, "window_type": "hamming",
                "use_energy": False, "fbank_centering_span": None,
            }),
        ):
            if not isinstance(values, dict) or any(
                name not in values or not matches(values[name], expected_value)
                for name, expected_value in expected.items()
            ):
                raise ValueError("unsupported Community-1 runtime metadata")
    except (KeyError, TypeError) as error:
        raise ValueError(f"invalid Community-1 manifest: {error}") from error


def _validate_plda(path):
    _, plda = _read_json(path)

    def vector(value, length):
        if not isinstance(value, list) or len(value) != length:
            return False
        for number in value:
            if type(number) not in (int, float):
                return False
            try:
                if not math.isfinite(number):
                    return False
            except OverflowError:
                return False
        return True

    mean2 = plda.get("mean2")
    projected = len(mean2) if isinstance(mean2, list) else 0
    lda, transform = plda.get("lda"), plda.get("tr")
    if (
        not 128 <= projected <= 256
        or not vector(plda.get("mean1"), 256)
        or not vector(mean2, projected)
        or not vector(plda.get("mu"), projected)
        or not vector(plda.get("phi"), 128)
        or any(value < 0 for value in plda["phi"])
        or not isinstance(lda, list) or len(lda) != 256
        or any(not vector(row, projected) for row in lda)
        or not isinstance(transform, list) or len(transform) != 128
        or any(not vector(row, projected) for row in transform)
    ):
        raise ValueError(f"{path}: invalid Community-1 PLDA dimensions or values")


def _copy_asset(source_root, destination_root, name, *, expected=None, required=True):
    source = source_root
    for component in Path(name).parts:
        source = source / component
        if source.is_symlink():
            raise ValueError(f"source export contains a symbolic link: {source}")
    if not source.exists() and not required:
        return None
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError(f"missing or empty source export file: {source}")
    destination = destination_root / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if expected is None:
        expected = _sha256(source)
    shutil.copyfile(source, destination)
    digest = _sha256(destination)
    if expected is not None and digest != expected:
        raise ValueError(f"source export hash mismatch: {name}")
    return digest


def _rename_noreplace(source, destination):
    """Atomically publish a directory without replacing even an empty target."""
    if sys.platform == "win32":
        os.rename(source, destination)  # Windows rename fails if the target exists.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        if sys.platform == "darwin":
            rename = libc.renamex_np
            rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            args = (os.fsencode(source), os.fsencode(destination), 0x00000004)  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            rename = libc.renameat2
            rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            args = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
        else:
            raise RuntimeError("atomic no-replace directory rename is unsupported on this platform")
    except AttributeError as error:
        raise RuntimeError("atomic no-replace directory rename is unavailable") from error
    rename.restype = ctypes.c_int
    if rename(*args) != 0:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise FileExistsError(error, os.strerror(error), str(destination))
        raise OSError(error, os.strerror(error), str(destination))


def quantize(model_dir, calibration_dir, output_dir):
    model_dir = Path(model_dir).resolve(strict=True)
    calibration_dir = Path(calibration_dir).resolve(strict=True)
    output = Path(output_dir).expanduser()
    if not output.name:
        raise ValueError("output must name a new directory")
    output_dir = output.parent.resolve(strict=True) / output.name
    if not model_dir.is_dir() or not calibration_dir.is_dir():
        raise ValueError("model and calibration paths must be directories")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"output already exists: {output_dir}")
    if output_dir.is_relative_to(model_dir) or output_dir.is_relative_to(calibration_dir):
        raise ValueError("output must not be inside the source export or calibration directory")

    manifest_path = model_dir / "export.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("missing or symbolic-link source export.json")
    manifest_bytes, manifest = _read_json(manifest_path)
    _validate_manifest(manifest)

    # All work is staged beside the destination, which is published only once.
    with tempfile.TemporaryDirectory(prefix=f".{output_dir.name}-", dir=output_dir.parent) as temporary:
        work = Path(temporary)
        staging, fp32 = work / "bundle", work / "bundle" / "fp32"
        fp32.mkdir(parents=True)
        (fp32 / "export.json").write_bytes(manifest_bytes)
        source_files = {"export.json": hashlib.sha256(manifest_bytes).hexdigest()}
        dynamic_files = ("segmentation.onnx", "embedding.onnx")
        for name in dynamic_files:
            digest = _copy_asset(
                model_dir, fp32, name, expected=manifest["runtime_sha256"].get(name), required=False
            )
            if digest is not None:
                source_files[name] = digest
        if sum(name in source_files for name in dynamic_files) not in (0, len(dynamic_files)):
            raise ValueError("source export must contain both dynamic graphs or neither")
        for name in RUNTIME_FILES:
            source_files[name] = _copy_asset(
                model_dir, fp32, name, expected=manifest["runtime_sha256"].get(name)
            )
        for name in dynamic_files:
            if name in source_files:
                _load_onnx(fp32 / name)
        for name, source_name in PROVENANCE_FILES.items():
            expected = manifest["source_sha256"].get(source_name)
            present = (model_dir / name).exists() or (model_dir / name).is_symlink()
            if present and not _valid_hash(expected):
                raise ValueError(f"missing or invalid source provenance hash: {source_name}")
            digest = _copy_asset(model_dir, fp32, name, expected=expected, required=False)
            if digest is not None:
                source_files[name] = digest
        _validate_plda(fp32 / "plda.json")

        sessions, paths, calibration = {}, {}, {}
        for kind in CONTRACTS:
            source_paths = sorted((calibration_dir / kind).glob("*.npz"))
            if not source_paths:
                raise ValueError(f"no calibration .npz files in {calibration_dir / kind}")
            sessions[kind] = _session(fp32 / "tract" / f"{kind}.onnx", kind)
            paths[kind], calibration[kind] = [], []
            target_dir = work / "calibration" / kind
            target_dir.mkdir(parents=True)
            for index, source in enumerate(source_paths):
                target = target_dir / f"{index}.npz"
                shutil.copyfile(source, target)
                batch = _load_batch(target, kind, sessions[kind].get_inputs())
                _run_and_validate(sessions[kind], kind, target, batch)
                paths[kind].append(target)
                calibration[kind].append({
                    "sha256": _sha256(target),
                    "inputs": {name: list(array.shape) for name, array in batch.items()},
                })

        (staging / "tract").mkdir()
        shutil.copyfile(fp32 / "plda.json", staging / "plda.json")
        _copy_asset(
            fp32, staging, "tract/segmentation.onnx",
            expected=source_files["tract/segmentation.onnx"],
        )
        _quantize_embedding_mixed(
            fp32 / "tract" / "embedding.onnx",
            staging / "tract" / "embedding.onnx",
            _NpzReader(paths["embedding"], "embedding", sessions["embedding"].get_inputs()),
        )
        errors, opsets = {}, {}
        for kind in CONTRACTS:
            output = staging / "tract" / f"{kind}.onnx"
            quantized = _session(output, kind)
            maximum = 0.0
            for path in paths[kind]:
                batch = _load_batch(path, kind, quantized.get_inputs())
                reference = _run_and_validate(sessions[kind], kind, path, batch)
                actual = _run_and_validate(quantized, kind, path, batch)
                maximum = max(maximum, float(np.max(np.abs(actual.astype(np.float64) - reference))))
            errors[kind] = maximum
            graph = onnx.load(str(output), load_external_data=False)
            default_opsets = [item.version for item in graph.opset_import if item.domain in ("", "ai.onnx")]
            if len(default_opsets) != 1:
                raise ValueError(f"{output}: expected one default ONNX opset")
            opsets[kind] = default_opsets[0]

        manifest["runtime_sha256"] = {name: _sha256(staging / name) for name in RUNTIME_FILES}
        manifest.pop("graph_check", None)  # The exporter's check applies only to fp32/.
        if len(set(opsets.values())) == 1:
            manifest["opset"] = next(iter(opsets.values()))
        else:
            manifest.pop("opset", None)
        manifest["segmentation"]["input"] = {"waveforms": [1, 1, 160000]}
        manifest["segmentation"]["output"] = {"scores": [1, 589, 7]}
        manifest["embedding"]["inputs"] = {"fbank": [1, 998, 80], "mask": [1, 589]}
        manifest["embedding"]["output"] = {"embedding": [1, 256]}
        manifest["quantization"] = {
            "format": "QOperator",
            "precision": {"segmentation": "FP32", "embedding": "mixed FP32/INT8"},
            "weight_type": "QInt8",
            "activation_type": {"segmentation": "FP32", "embedding": "QUInt8"},
            "segmentation_operators": [],
            "embedding_operators": ["Conv"],
            "per_channel": True,
            "excluded_nodes": [FIRST_CONV],
            "opsets": opsets,
            "onnx_version": onnx.__version__,
            "onnxruntime_version": ort.__version__,
            "source_export_files_sha256": source_files,
            "calibration": calibration,
            "calibration_max_absolute_error": errors,
            "validation_provider": "ONNX Runtime CPU",
            "quality_threshold": None,
            "tract_execution_verified": False,
        }
        original_limitations = manifest.get("limitations", "")
        if not isinstance(original_limitations, str):
            raise ValueError("invalid source export limitations")
        manifest["limitations"] = (
            f"FP32 source: {original_limitations} "
            "Mixed-precision validation covers only supplied calibration arrays on ONNX Runtime CPU; "
            "no quality threshold or Tract execution was verified. Diarization quality, "
            "parity, speed and target-platform support are not established."
        )
        (staging / "export.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        _rename_noreplace(staging, output_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-dir", type=Path, required=True, help="original FP32 exporter bundle")
    parser.add_argument("--calibration-dir", type=Path, required=True, help="directory containing calibration batches")
    parser.add_argument("--output-dir", type=Path, required=True, help="new native mixed FP32/INT8 bundle directory")
    args = parser.parse_args()
    try:
        quantize(args.model_dir, args.calibration_dir, args.output_dir)
    except Exception as error:
        parser.exit(2, f"quantization failed: {error}\n")
    print("Validated supplied batches with ONNX Runtime CPU; Tract execution, quality and speed are not established.")


if __name__ == "__main__":
    main()
