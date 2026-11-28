#!/usr/bin/env python3
"""Produce an offline Community-1 INT8 proof bundle using public AMI audio.

Usage: python packaging/rust-liquid/ami_int8_ci_bundle.py --source DIR --audio WAV --output DIR

The source must contain the four pinned community-1-fp32-v1 runtime files and
WAV must be the pinned ES2004a Mix-Headset recording. Nothing is downloaded.
The output parent must exist and output itself must not exist. Calibration
arrays and the audio are kept only in a private temporary directory; the bundle
contains their hashes and provenance, not the data. This is an experiment, not
a quality, cross-platform reproducibility, speed, or release-readiness claim.

The frontend uses CPU PyTorch/torchaudio, soundfile and ONNX Runtime. Record the
actual package and platform versions because numerical results can differ across
environments even with the same inputs.
"""

import argparse
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import soundfile as sf
import torch
import torchaudio


# The existing proof producer owns the independently pinned public FP32 assets
# and loads the public quantizer's validation and no-replace publication helpers.
_BASE_SCRIPT = Path(__file__).resolve().parent / "int8_ci_bundle.py"
_spec = importlib.util.spec_from_file_location("community_int8_ci_bundle", _BASE_SCRIPT)
if _spec is None or _spec.loader is None:
    raise ImportError("Cannot load Community-1 proof bundle helpers")
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
_support = _base._support
PINNED_ASSETS = _base.PINNED_ASSETS
QUANTIZE_SCRIPT = _base.QUANTIZE_SCRIPT

AUDIO_URL = "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2004a/audio/ES2004a.Mix-Headset.wav"
AUDIO_SIZE = 33579394
AUDIO_SHA256 = "3e2560b19bee6952c7c7ce041b0f1ea8a7ea9468044c4eea79d2a2c67e24ab0f"
REFERENCE_EMBEDDING_SHA256 = "4eee8a8bc6a99781b3f3f414cbb0cd091b2b66b3c1da108a2e3246cd434980ee"
SAMPLE_RATE = 16000
WINDOW_SECONDS = 10
WINDOW_STARTS = (24, 120, 180, 376)
MIN_MASK_FRAMES = 20
POWERSET_BITS = (0, 1, 2, 4, 3, 5, 6)
FBANK_OPTIONS = {
    "num_mel_bins": 80,
    "frame_length": 25.0,
    "round_to_power_of_two": True,
    "frame_shift": 10.0,
    "snip_edges": True,
    "dither": 0.0,
    "sample_frequency": SAMPLE_RATE,
    "window_type": "hamming",
    "use_energy": False,
}


def _save_batch(calibration, kind, filename, inputs, **details):
    path = calibration / kind / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **inputs)
    return {
        "sha256": _support._sha256(path),
        "inputs": {name: _base._array_record(value) for name, value in inputs.items()},
        **details,
    }


def _create_calibration(audio_path, fp32, calibration):
    """Generate the historical window and clean-single-speaker mask recipe."""
    segmentation_path = fp32 / "tract/segmentation.onnx"
    _support._load_onnx(segmentation_path)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    segmentation = ort.InferenceSession(
        str(segmentation_path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    records = {}
    bits_by_class = np.asarray(POWERSET_BITS, dtype=np.uint8)
    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(2)
        with sf.SoundFile(str(audio_path)) as audio, torch.no_grad():
            if audio.samplerate != SAMPLE_RATE or audio.channels != 1:
                raise ValueError("AMI audio must be mono at 16000 Hz")
            samples = WINDOW_SECONDS * SAMPLE_RATE
            if len(audio) < (max(WINDOW_STARTS) + WINDOW_SECONDS) * SAMPLE_RATE:
                raise ValueError("AMI audio is too short for the calibration windows")
            audio_info = {
                "sample_rate": audio.samplerate,
                "channels": audio.channels,
                "frames": len(audio),
                "format": audio.format,
                "subtype": audio.subtype,
            }
            for start in WINDOW_STARTS:
                audio.seek(start * SAMPLE_RATE)
                waveform = audio.read(samples, dtype="float32", always_2d=False)
                if waveform.shape != (samples,) or not np.isfinite(waveform).all():
                    raise ValueError(f"invalid audio in calibration window {start}")
                waveforms = waveform.reshape(1, 1, samples)
                scores = _support._run_and_validate(
                    segmentation, "segmentation", segmentation_path, {"waveforms": waveforms}
                )
                bits = bits_by_class[scores.argmax(axis=-1)[0]]
                features = torch.vmap(
                    lambda waveform: torchaudio.compliance.kaldi.fbank(waveform, **FBANK_OPTIONS)
                )(torch.from_numpy(waveforms) * 32768)
                features = features - features.mean(dim=1, keepdim=True)
                fbank = features.numpy().astype(np.float32, copy=False)
                if fbank.shape != (1, 998, 80) or not np.isfinite(fbank).all():
                    raise ValueError(f"invalid fbank in calibration window {start}")

                key = f"segmentation/{start}.npz"
                records[key] = _save_batch(
                    calibration, "segmentation", f"{start}.npz", {"waveforms": waveforms},
                    start_seconds=start,
                )
                for speaker in range(3):
                    mask = (bits == (1 << speaker)).astype(np.float32)[None, :]
                    frame_count = int(mask.sum())
                    if frame_count < MIN_MASK_FRAMES:
                        continue
                    key = f"embedding/{start}-{speaker}.npz"
                    records[key] = _save_batch(
                        calibration, "embedding", f"{start}-{speaker}.npz",
                        {"fbank": fbank, "mask": mask}, start_seconds=start,
                        local_speaker=speaker, single_speaker_frames=frame_count,
                    )
    finally:
        torch.set_num_threads(previous_threads)
    if not any(name.startswith("embedding/") for name in records):
        raise ValueError("no single-speaker calibration masks meet the minimum length")
    return audio_info, records


def _expected_calibration(records):
    return {
        kind: [
            {
                "sha256": record["sha256"],
                "inputs": {name: item["shape"] for name, item in record["inputs"].items()},
            }
            for path, record in sorted(records.items()) if path.startswith(f"{kind}/")
        ]
        for kind in ("segmentation", "embedding")
    }


def _check_output_files(staging):
    expected = set(PINNED_ASSETS) | {f"fp32/{name}" for name in PINNED_ASSETS}
    actual = set()
    for path in staging.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"CLI output contains a symbolic link: {path.relative_to(staging)}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"CLI output contains a non-file: {path.relative_to(staging)}")
        actual.add(path.relative_to(staging).as_posix())
    if actual != expected:
        raise ValueError("CLI output file set mismatch")


def build_bundle(source, audio, output):
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("source must be a directory")
    audio = Path(audio).expanduser()
    audio = audio.parent.resolve(strict=True) / audio.name
    output = Path(output).expanduser()
    if output.name in ("", ".", ".."):
        raise ValueError("output must name a new directory")
    output = output.parent.resolve(strict=True) / output.name
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists: {output}")
    if output.is_relative_to(source):
        raise ValueError("output must not be inside the source directory")

    # Verify a private copy before any audio parser (or model parser) sees it.
    # Calibration data and incomplete bundles are removed even on interruption.
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        work = Path(temporary)
        if not audio.is_file() or audio.stat().st_size != AUDIO_SIZE:
            raise ValueError("AMI audio missing or size mismatch")
        audio_snapshot = work / "audio" / audio.name
        _support._copy_asset(audio.parent, audio_snapshot.parent, audio.name, expected=AUDIO_SHA256)
        if audio_snapshot.stat().st_size != AUDIO_SIZE:
            raise ValueError("AMI audio size changed while copying")

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

        calibration = work / "calibration"
        audio_info, records = _create_calibration(audio_snapshot, fp32, calibration)
        staging = work / "bundle"
        subprocess.run(
            [
                sys.executable, str(QUANTIZE_SCRIPT),
                "--model-dir", str(fp32),
                "--calibration-dir", str(calibration),
                "--output-dir", str(staging),
            ],
            check=True,
            env={**os.environ, "OMP_NUM_THREADS": "2"},
        )
        _check_output_files(staging)
        _, output_manifest = _support._read_json(staging / "export.json")
        quantization = output_manifest["quantization"]
        source_hashes = {name: record["sha256"] for name, record in source_records.items()}
        for name, expected in source_hashes.items():
            if _support._sha256(staging / "fp32" / name) != expected:
                raise ValueError(f"CLI source snapshot hash mismatch: {name}")
        if quantization["source_export_files_sha256"] != source_hashes:
            raise ValueError("CLI source provenance hash mismatch")
        if quantization["calibration"] != _expected_calibration(records):
            raise ValueError("CLI calibration provenance mismatch")

        qconv_count = _support._validate_quantized_graph(
            fp32 / "tract/embedding.onnx", staging / "tract/embedding.onnx"
        )
        output_hashes = {name: _support._sha256(staging / name) for name in PINNED_ASSETS}
        if output_manifest["runtime_sha256"] != {
            name: output_hashes[name] for name in _support.RUNTIME_FILES
        }:
            raise ValueError("CLI runtime hash mismatch")
        for name in ("plda.json", "tract/segmentation.onnx"):
            if output_hashes[name] != source_hashes[name]:
                raise ValueError(f"CLI changed an FP32 runtime asset: {name}")

        embedding_sha256 = output_hashes["tract/embedding.onnx"]
        proof = {
            "schema_version": 1,
            "proof_only": True,
            "source_release": "community-1-fp32-v1",
            "source_files": source_records,
            "output_files_sha256": output_hashes,
            "quantization": {
                "format": quantization["format"],
                "operators": quantization["embedding_operators"],
                "per_channel": quantization["per_channel"],
                "activation_type": quantization["activation_type"]["embedding"],
                "weight_type": quantization["weight_type"],
                "excluded_nodes": quantization["excluded_nodes"],
                "qlinearconv_count": qconv_count,
                "quantizer_sha256": _support._sha256(QUANTIZE_SCRIPT),
            },
            "calibration": {
                "synthetic": False,
                "source": {
                    "name": "AMI ES2004a Mix-Headset",
                    "url": AUDIO_URL,
                    "license": "CC-BY-4.0",
                    "size": AUDIO_SIZE,
                    "sha256": AUDIO_SHA256,
                    **audio_info,
                },
                "window_starts_seconds": list(WINDOW_STARTS),
                "window_seconds": WINDOW_SECONDS,
                "powerset_class_bits": list(POWERSET_BITS),
                "minimum_single_speaker_frames": MIN_MASK_FRAMES,
                "waveform_scale_before_fbank": 32768,
                "fbank_options": FBANK_OPTIONS,
                "fbank_centering": "per-window mean across frames",
                "files": records,
            },
            "reference_comparison": {
                "reference_embedding_sha256": REFERENCE_EMBEDDING_SHA256,
                "embedding_sha256": embedding_sha256,
                "result": "match" if embedding_sha256 == REFERENCE_EMBEDDING_SHA256 else "mismatch",
                "scope": "byte equality only; neither result establishes quality or platform parity",
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
                "platform": platform.platform(),
                "machine": platform.machine(),
                "numpy": np.__version__,
                "onnx": onnx.__version__,
                "onnxruntime": ort.__version__,
                "torch": torch.__version__,
                "torchaudio": torchaudio.__version__,
                "soundfile": sf.__version__,
                "libsndfile": sf.__libsndfile_version__,
                "torch_intra_op_threads": 2,
                "segmentation_onnx_intra_op_threads": 2,
                "quantizer_omp_num_threads": 2,
            },
        }
        (staging / "proof-manifest.json").write_text(
            json.dumps(proof, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
        _support._rename_noreplace(staging, output)
    return embedding_sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="directory with the four pinned FP32 assets")
    parser.add_argument("--audio", type=Path, required=True, help="pinned AMI ES2004a Mix-Headset WAV")
    parser.add_argument("--output", type=Path, required=True, help="new output directory (parent must exist)")
    args = parser.parse_args()
    try:
        digest = build_bundle(args.source, args.audio, args.output)
        proof_digest = _support._sha256(args.output.expanduser() / "proof-manifest.json")
    except Exception as error:
        parser.exit(2, f"proof bundle failed: {error}\n")
    print(f"embedding_sha256={digest}")
    print(f"proof_manifest_sha256={proof_digest}")
    print(f"embedding_reference_match={'match' if digest == REFERENCE_EMBEDDING_SHA256 else 'mismatch'}")
    print("Proof only: no diarization quality, speed, Tract execution, platform parity, or release claim.")


if __name__ == "__main__":
    main()
