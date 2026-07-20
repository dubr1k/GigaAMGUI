# GigaAM Transcriber 1.3.1

Release 1.3.1 fixes [issue #32](https://github.com/dubr1k/GigaAMGUI/issues/32)
and makes model preparation and accelerator selection consistent across the
desktop portable builds.

## What changed

- Native Windows never imports NeMo for Sortformer. It uses the pinned
  Streaming Sortformer v2.1 ONNX export, verifies its SHA-256 and graph
  interface, and downloads it to the persistent user cache after the first
  **Start processing** click.
- Windows and Linux now ship exactly one `onnxruntime-gpu` distribution. Its
  provider order is CUDA → CPU; the CPU provider is included in the same wheel.
- ONNX Runtime reuses CUDA/cuDNN from the application's selected `cu124` or
  `cu128` PyTorch runtime through `onnxruntime.preload_dlls()`. This keeps
  PyTorch ASR and ONNX Sortformer on the same GPU when CUDA is selected.
- macOS ONNX uses CoreML → CPU. Native NeMo Sortformer uses MPS on Apple Silicon
  and retries once on CPU only if MPS transfer or inference fails.
- DirectML and TensorRT remain explicit advanced providers. They no longer take
  priority over CUDA in `auto` mode.
- Selecting CPU does not download a CUDA runtime.

## Model preparation after Start processing

The application now builds and completes a preparation plan before processing
the first file. The plan contains only the selected stages: GigaAM ASR, optional
DeepFilterNet, and the selected pyannote/ONNX/Sortformer diarization backend.

For every component the processing log shows cache checking, download, load,
ready/failure state, and—where applicable—the actual device/provider chain.
Missing artifacts are downloaded into the writable application cache and reused
by the remaining files and later launches. A preparation failure names the
component and stops the queue before partial processing begins.

The offline archive remains read-only. It includes the base ONNX ASR, VAD, and
Pyannote+WeSpeaker diarization chain. Models not included in that archive,
including Sortformer and multilingual/MLX variants, are downloaded to the user
cache when selected and a network connection is available.

Pyannote behavior is unchanged: a valid Hugging Face read token and accepted
terms for all gated repositories are required. Access failures retain the real
repository/token error and never silently label the whole file as one speaker.

## Accelerator validation completed

- Apple Silicon CoreML: real Sortformer ONNX streaming inference completed with
  session providers `CoreMLExecutionProvider, CPUExecutionProvider`; ORT
  delegated 18 graph partitions.
- Apple Silicon MPS: GigaAM PyTorch and native NeMo Sortformer v2.1 completed
  real inference with `PYTORCH_ENABLE_MPS_FALLBACK=0`; the two-speaker smoke
  returned two speakers on `mps`.
- NVIDIA CUDA on Linux: real Sortformer ONNX streaming inference completed on a
  GTX 1650 with session providers `CUDAExecutionProvider,
  CPUExecutionProvider`.

Full implementation details are in
[`docs/CHANGELOG.md`](https://github.com/dubr1k/GigaAMGUI/blob/main/docs/CHANGELOG.md).
