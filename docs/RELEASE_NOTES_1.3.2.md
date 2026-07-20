# GigaAM Transcriber 1.3.2

Release 1.3.2 fixes [issue #33](https://github.com/dubr1k/GigaAMGUI/issues/33):
decoder-overlap text is no longer duplicated at adjacent ASR chunk boundaries,
and diarized exports no longer receive overlapping timestamps from those chunks.

## What changed

- The 2-second decoder overlap remains enabled to preserve recognition context.
- Boundary stitching now handles the decoder variations reported in issue #33,
  including a short divergent prefix and a filler word present in only one pass.
- PyTorch, MLX, and ONNX use the same reconciliation path: the matched prefix is
  removed once, retained word timestamps are clipped to the chunk's nominal
  ownership interval, and segment text is rebuilt from the retained words.
- Diarization validates the resulting timeline before formatting. Exactly
  touching turns from the same speaker may merge, while real pauses remain
  separate and corrupt overlaps fail explicitly instead of reaching TXT, MD,
  SRT, or VTT output.
- Text-only transcription remains available when a backend does not provide
  usable word timestamps.

## Validation

Regression coverage includes both phrase patterns from issue #33, jittered word
timestamps on both sides of a nominal cut, all three ASR backends, diarization
timeline validation, and the existing formatter suite.

Full implementation details are in
[`docs/CHANGELOG.md`](https://github.com/dubr1k/GigaAMGUI/blob/main/docs/CHANGELOG.md).
