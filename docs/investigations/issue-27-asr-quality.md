# Issue #27: GigaAM recognition quality investigation

Date: 2026-07-14

Branch: `investigate/issue-27-asr-quality`

Issue: <https://github.com/dubr1k/GigaAMGUI/issues/27>

## Executive conclusion

The reported difference is reproducible and is caused by **speech-boundary selection from the official GigaAM long-form VAD path**, not by a different ASR checkpoint, ONNX, FFmpeg resampling, GUI post-processing, or the 20-second boundary alone.

On the attached `Phone_ARU_ON.wav`, the current GUI/PyTorch backend decodes the whole opening context and returns:

> Ирина, алло, Ольга, здравствуйте.

Official `GigaAMASR.transcribe_longform()` first runs `pyannote/segmentation-3.0`, selects the speech interval `3.11909375–19.65659375`, and returns:

> Три ноля, Ольга, здравствуйте.

The official result and boundaries match the reporter's example.

## Tested source and artifacts

- GigaAMGUI: `0e1a0ac59a6816eefc7a18d782d11cc9ca14f379` (`origin/main` at investigation start)
- Official GigaAM: `559d88d6b72541412743929f633a6ae7c9950b85`
- Model: `v3_e2e_rnnt`
- Local checkpoint MD5: `2730de7545ac43ad256485a462b0a27a`
- Official expected checkpoint MD5: `2730de7545ac43ad256485a462b0a27a`
- Input duration: `20.581587 s`, mono PCM S16LE, 11025 Hz
- Test runtime: CPU, PyTorch 2.10.0, torchaudio 2.10.0, GigaAM 0.2.0
- VAD runtime: pyannote.audio 4.0.7, `pyannote/segmentation-3.0`

## Differential results

| Path | Input presented to ASR | Opening recognition |
|---|---:|---|
| Current GUI `PyTorchBackend.transcribe_longform()` | `0.000–20.000` plus a silent `20.000–20.582` tail | `Ирина, алло, Ольга...` |
| Official `model.transcribe()` | complete `0.000–20.582` file | `Ирина, алло, Ольга...` |
| Direct `forward + decode`, complete file | complete `0.000–20.582` file | `Ирина, алло, Ольга...` |
| Official `model.transcribe_longform()` | VAD interval `3.11909375–19.65659375` | `Три ноля, Ольга...` |

Repeated complete-file and 20-second decodes were deterministic.

### Exact official long-form output

```json
{
  "segments": [
    {
      "start": 3.11909375,
      "end": 19.656593750000003,
      "text": "Три ноля, Ольга, здравствуйте. Здравствуйте, это вызов такси? Да. Подскажите, пожалуйста, сколько будет стоить проезд от проспекта Просвещения до аэропорта? Пулково, первое, второе. Второе. Одну секунду, пожалуйста. Стоимость поездки будет 980 ₽. Спасибо. Пожалуйста, всего доброго."
    }
  ]
}
```

## Hypotheses

### Confirmed: official VAD boundary changes recognition

The current GUI bypasses the public GigaAM long-form path and performs fixed 20-second decoding in `src/core/asr/pytorch_backend.py`:

```python
chunk_size = 20 * sample_rate
...
encoded, encoded_len = model.forward(wav, length)
decode_result = model.decoding.decode(model.head, encoded, encoded_len)
```

The official long-form method calls `segment_audio_file()`, backed by `pyannote/segmentation-3.0`, before ASR.

The model is highly boundary-sensitive on this sample. A 9×9 matrix around the VAD interval varied start and end by `±10/20/50/100 ms`:

- 81 combinations tested;
- all 9 combinations with the exact VAD start recognized `Три ноля` regardless of tested end perturbation;
- every tested start perturbation, including only ±10 ms, changed the opening recognition;
- alternatives included `Ирина, алло`, `Ирина, алле`, `Ирина Ольга`, and `Алло`.

Thus the important variable is the VAD-selected start sample, not text formatting.

### Rejected for this sample: 20.0 s versus 20.58 s alone

The GUI does split the 20.58-second file at 20 seconds, but official short-form decoding of the complete 20.58-second input produces the same incorrect opening as the GUI. Prefixes from 18.0 through 20.5 seconds also retained the same opening.

Fixed 20-second chunking remains a general quality risk for long recordings because it can cut through speech, but it does not explain the specific `Три ноля` result without VAD.

### Rejected: different checkpoint or ONNX

The GUI used the official PyTorch `v3_e2e_rnnt` checkpoint. Its MD5 exactly matches GigaAM's expected hash. No ONNX inference is used in the current PyTorch backend.

### Rejected: FFmpeg/resampling

GUI conversion and official `gigaam.load_audio()` were converted to raw 16 kHz mono S16LE and compared. Their SHA-256 hashes were identical:

```text
8c5eadc37d6505bfbb07874bfc56fddb0e6e21c740b4daecd7a98d7181168909
```

### Rejected: post-processing

The main transcription path strips and formats decoder output but contains no replacement capable of turning `Три ноля` into `Ирина, алло`.

## Why the official VAD method cannot simply replace the current backend today

The production dependency set and the pinned official GigaAM long-form implementation are currently misaligned:

- GigaAM 0.2 declares its supported long-form extra with `pyannote.audio==4.0.*`, PyTorch 2.10, torchaudio 2.10, and torchcodec 0.10.
- GigaAMGUI currently pins `pyannote.audio==3.1.1` and PyTorch/torchaudio `<2.9`, plus compatibility monkey patches.
- Running official long-form with the supported 4.0 stack succeeds and reproduces the reporter's text.
- Running it with the application's 3.1.1 stack fails because current GigaAM resolves a local segmentation snapshot and passes its directory to `Model.from_pretrained`; pyannote 3.1.1 interprets that directory as a Hugging Face repo ID and raises `HFValidationError`.

Therefore replacing fixed chunks with `model.transcribe_longform()` in one line would break the packaged application.

## Recommended implementation direction

Do not patch the phrase or merely change 20 seconds to 25 seconds. Those changes do not reproduce the quality improvement.

Recommended staged approach:

1. Add an ASR segmentation abstraction independent of diarization.
2. Implement a pyannote-3.1-compatible VAD adapter using the application's existing compatibility layer and token handling.
3. Preserve a clearly reported fixed-chunk fallback when VAD is unavailable.
4. Add a visible quality mode/backend diagnostic so users know whether VAD or fixed chunks were used.
5. Preserve segment boundaries and progress callbacks through the adapter.
6. Add an opt-in integration corpus with issue #27 plus several licensed long recordings; compare text/WER and boundaries for VAD versus fallback.
7. Only then consider migrating the packaged runtime to pyannote 4 / torch 2.10 as a separate change, because that affects Windows/Linux/macOS builds and diarization.

A smaller alternative is an optional `ASR_USE_VAD` mode, but it must fail explicitly or fall back visibly when the gated segmentation model/token is unavailable.

## Acceptance checks

- Issue #27 fixture recognizes `Три ноля, Ольга, здравствуйте` in PyTorch VAD quality mode.
- Without `HF_TOKEN`, a cached VAD model still works offline; otherwise transcription uses an explicit diagnosed fallback.
- A missing/inaccessible segmentation model does not silently produce empty output.
- Segment boundaries remain monotonic and within media duration.
- Long audio is never cut at a fixed boundary through active speech when VAD mode is available.
- CPU and CUDA builds use the same checkpoint and tokenizer hashes.
- Existing unit suite, packaged self-checks, and platform build workflows pass.

## Implemented remediation

The investigation branch now contains a production implementation rather than the unsafe one-line `transcribe_longform()` switch:

- PyTorch and MLX ASR use a shared `pyannote/segmentation-3.0` adapter compatible with the pinned `pyannote.audio 3.1.1` runtime.
- `ASR_SEGMENTATION_MODE=vad` is the explicit default; `fixed_chunks` preserves the legacy path when deliberately selected.
- VAD runs on CPU by default so that GigaAM and the segmentation model do not compete for accelerator memory.
- Cached VAD weights work without a token; unavailable VAD falls back to fixed chunks with sanitized health diagnostics.
- Shared VAD and GigaAM inference is serialized because API frontends reuse one model loader across concurrent jobs.
- Exact VAD metadata boundaries are retained while sample indices are quantized only for waveform slicing.
- MLX re-splits long VAD regions with its native silence-aware splitter so every decoder input remains at most 20 seconds.

Pinned-stack end-to-end verification on the issue attachment produced one VAD interval at
`3.1154499151103567–19.668930390492363`, active mode `vad`, no fallback, and the expected opening
`Три ноля, Ольга, здравствуйте`. Mono and generated stereo copies produced identical VAD boundaries.

Applying the same VAD segmentation to MLX did not reproduce the expected opening on the issue
attachment. The MLX implementation preserves VAD boundaries, progress, chunk limits, and fallback
diagnostics, but it is not considered a confirmed recognition-quality fix for issue #27. The
remaining difference requires a separate MLX decoder/backend investigation.

The implementation was released in `v1.1.8` with the MLX limitation above documented explicitly.
