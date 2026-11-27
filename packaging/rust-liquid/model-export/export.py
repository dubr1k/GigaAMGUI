"""Export pinned GigaAM graphs for packaging on an isolated Linux builder.

The Hugging Face model code executes during export. Only the two immutable
revisions below are accepted. Output appears under OUTPUT/model only after all
graphs, their source licence and provenance have been written successfully.
An export is not a parity, quality, licensing-review or publication approval.
"""

import argparse
import importlib
import json
import platform
import re
import shutil
import tempfile
from importlib.metadata import version
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from transformers import AutoModel


MODELS = {
    "v4": (
        "ai-sage/GigaAM-v4",
        "9fe3ebf50c9e0cce5eed60d546938632238b6a96",
        15_000_000_000,
    ),
    "multilingual": (
        "ai-sage/GigaAM-Multilingual-v2",
        "c21483b5e185ff32f6fb2e2c801f92f4e7fbfee8",
        30_000_000_000,
    ),
}
FUTURE_CONTEXT_MS = 320


def export_streaming(model, module, output):
    """Copy upstream's fixed-context streaming frontend/step export sequence."""
    encoder = model.encoder
    cfg = model.cfg
    contexts = {int(item.future_context_ms): item for item in cfg.streaming.contexts}
    context = contexts[FUTURE_CONTEXT_MS]
    factor = int(encoder.subsampling_factor)
    frame_ms = float(encoder.frame_shift_ms)
    chunk = int(FUTURE_CONTEXT_MS / frame_ms)
    if chunk <= 0 or chunk * frame_ms != FUTURE_CONTEXT_MS:
        raise ValueError("Unsupported streaming frame geometry")
    left = int(context.left_context)
    device = model._device
    encoder._onnx_context = FUTURE_CONTEXT_MS
    encoder.export_language_dim = (
        model.language_embedding.weight.shape[0]
        if model.language_embedding is not None
        else 0
    )

    # Upstream first warms the native encoder to materialize cache shapes.
    warm = encoder.create_stream_state(chunk, left)
    example = torch.zeros(
        1,
        int(cfg.preprocessor.features),
        factor * (left + chunk),
        device=device,
        dtype=next(encoder.parameters()).dtype,
    )
    lengths = torch.tensor([example.shape[-1]], device=device)
    kwargs = {}
    if model.language_embedding is not None:
        kwargs["lang_embedding"] = model.language_embedding.weight.new_zeros(
            1, model.language_embedding.weight.shape[1]
        )
    with torch.no_grad():
        encoder(example, lengths, state=warm, final=True, **kwargs)
    states = [torch.zeros_like(value) for layer in warm.layers for value in layer.tensors()]
    front = [torch.zeros_like(value) for value in warm.frontend]
    for tensor in (*states, *front):
        if tensor.dtype not in (torch.float32, torch.int32, torch.int64):
            raise ValueError(f"Unsupported cache dtype: {tensor.dtype}")
    schema = module._state_schema
    prefix = f"{cfg.model_name}_{FUTURE_CONTEXT_MS}ms"
    front_name = f"{prefix}_stream_frontend"
    step_name = f"{prefix}_stream_step"
    mel = example[:, :, : chunk * factor]
    lengths = torch.tensor([mel.shape[-1]], device=device)

    old_forward = encoder.forward
    try:
        encoder.forward = encoder.forward_frontend_for_export
        module.onnx_converter(
            front_name,
            encoder,
            str(output),
            (mel, lengths, *front),
            ["features", "feature_lengths", *[f"front_{i}" for i in range(len(front))]],
            ["pre_encoded", "encoded_lengths", *[f"next_front_{i}" for i in range(len(front))]],
            {"features": {2: "mel_frames"}, "pre_encoded": {1: "frames"}},
            export_dtype=None,
        )
    finally:
        encoder.forward = old_forward

    encoder._onnx_context = (chunk, left, False)
    encoder._onnx_caches = states
    old_forward = model.forward
    try:
        model.forward = model.forward_for_export
        module.onnx_converter(
            step_name,
            model,
            str(output),
            encoder.input_example(),
            encoder.input_names(),
            encoder.output_names(),
            encoder.dynamic_axes(),
            export_dtype=None,
        )
    finally:
        model.forward = old_forward

    manifest = {
        "model_name": prefix,
        "future_context_ms": FUTURE_CONTEXT_MS,
        "batched": False,
        "subsampling_factor": factor,
        "frame_shift_ms": frame_ms,
        "encoder_dim": int(encoder.feat_out),
        "frontend": {"file": f"{front_name}.onnx", "states": schema(front)},
        "step": {"file": f"{step_name}.onnx"},
        "states": schema(states),
    }
    (output / "streaming.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


MIT_LICENSE = """MIT License

Copyright (c) 2026 Giga

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=MODELS)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if platform.python_version_tuple()[:2] != ("3", "11"):
        parser.error("export requires Python 3.11")
    packages = {}
    for line in Path(__file__).with_name("requirements.txt").read_text(encoding="utf-8").splitlines():
        name, expected = line.split("==", 1)
        actual = version(name)
        if actual != expected:
            parser.error(f"{name} must be {expected}, found {actual}")
        packages[name] = actual
    repo, revision, minimum_free = MODELS[args.model]
    output = args.output
    if not output.is_absolute() or not output.parent.is_dir() or output.exists():
        parser.error("output must be a new absolute path under an existing directory")
    if shutil.disk_usage(output.parent).free < minimum_free:
        parser.error(f"at least {minimum_free} free bytes are required before export")
    output.mkdir(exist_ok=False)
    try:
        snapshot = Path(snapshot_download(repo_id=repo, revision=revision))
        if snapshot.name != revision or snapshot.parent.name != "snapshots":
            raise ValueError("Hugging Face did not return the pinned snapshot")
        card = (snapshot / "README.md").read_text(encoding="utf-8")
        license_text = (snapshot / "LICENSE").read_text(encoding="utf-8").strip()
        if not re.search(r"(?m)^license:\s*mit\s*$", card) or license_text != MIT_LICENSE:
            raise ValueError("pinned source licence is not the expected MIT licence")
        stage = Path(tempfile.mkdtemp(prefix=".export-", dir=output))
        wrapper = AutoModel.from_pretrained(
            str(snapshot), local_files_only=True, trust_remote_code=True, torch_dtype=torch.float32,
        ).eval()
        module = importlib.import_module(type(wrapper).__module__)
        # A zero context value follows the upstream unbounded/full-context branch
        # but exports the encoder and e2e heads separately, not the fused graph.
        wrapper.model._to_onnx(str(stage), torch.float32, future_context_ms=0)
        export_streaming(wrapper.model, module, stage)
        (stage / "MODEL_LICENSE.txt").write_text(license_text + "\n", encoding="utf-8")
        provenance = {
            "repo": repo,
            "revision": revision,
            "license": "MIT",
            "python": platform.python_version(),
            "packages": packages,
        }
        (stage / "MODEL_PROVENANCE.json").write_text(
            json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        stage.rename(output / "model")
    except BaseException:
        shutil.rmtree(output)
        raise


if __name__ == "__main__":
    main()
