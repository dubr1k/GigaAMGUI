# Issue #33: overlap-word ownership and monotonic exports

Date: 2026-07-20

Release: `v1.3.2`

## Context

Issue #33 shows two related regressions in a timecoded transcript:

1. adjacent exported entries overlap by about two seconds;
2. phrases decoded in the overlap are repeated because the two decoder
   hypotheses differ slightly.

The ASR chunk planner deliberately maintains two timelines. Decoder windows
overlap by two seconds to preserve acoustic context around a forced cut, while
`AudioChunk.start_sec/end_sec` define adjacent, non-overlapping ownership
intervals for exported results. Word timestamps are currently made absolute
from `decode_start_sample`, so they may fall outside the nominal ownership
interval. Speaker mapping then rebuilds turns from those raw word timestamps
and discards the nominal segment boundary, making the internal decoder overlap
visible in Markdown, SRT, VTT, and timecoded TXT output.

The textual fallback has a separate weakness. `stitch_overlapping_text` only
accepts a high equal-length fuzzy match or an alignment with a contiguous
prefix anchor. Both examples from issue #33 fall outside those rules:

- `вопросам в мире были едины` / `во всем мире были едины`;
- `хозяйством э-э медициной` / `хозяйством медициной образованием`.

The fix must restore one global ownership timeline without removing the
overlapping acoustic context that improved long-form recognition in `v1.1.9`.

## Requirements

- Keep the existing two-second overlap between decoder windows.
- Export adjacent ASR segments with monotonic, non-overlapping boundaries.
- Ensure every timestamped word belongs to exactly one nominal chunk interval.
- Keep `segment["transcription"]` consistent with `segment["words"]` after
  overlap reconciliation.
- Apply the same behavior to PyTorch, MLX, and ONNX ASR backends.
- Preserve the textual stitching fallback for decoders that do not provide
  usable word timestamps.
- Remove the repeated prefixes shown in issue #33 without deleting unrelated
  short phrases or legitimate sentence starts.
- Diarization must not reintroduce backward or overlapping turns.
- Plain TXT, timecoded TXT, Markdown, SRT, and VTT must consume the same
  reconciled utterance stream.
- Ship the correction as `v1.3.2` with updated release metadata and notes.

## Considered approaches

### Post-export clamping

Clamp each formatted entry to the end of the previous entry. This is small but
only hides the timecode symptom. It leaves duplicate words in the transcript,
can create zero-length cues, and assigns misleading times to speech. Rejected.

### Alignment followed by nominal clipping

Retain overlapping decoder windows and use the two text hypotheses to select a
single copy of repeated boundary words. Trim the matched prefix from the next
window, then clip surviving word timestamps to the nominal interval of the
segment that retained them. This preserves a boundary word even when the two
decoders jitter its midpoint to opposite sides of the cut, while preventing its
decoder-context timestamp from leaking into exports. Selected.

### Global decoder-lattice merge

Merge the two complete decoder hypotheses with a global timestamp/text lattice.
This could recover a word missed by the nominal owner, but it substantially
changes recognition semantics and needs a larger evaluation corpus. Deferred.

## Selected architecture

### Shared boundary-normalization helper

Add a backend-independent helper in the shared ASR layer. It accepts absolute
word timestamps, nominal segment boundaries, and the number of prefix words
removed by overlap alignment. It returns a normalized list:

- remove exactly the prefix count reported by `stitch_overlapping_text`;
- preserve the decoder hypothesis selected by that alignment instead of making
  an independent midpoint decision;
- clip every retained word to `chunk.start_sec/chunk.end_sec`, so a
  boundary-spanning word remains present once but cannot overlap the next
  nominal segment;
- omit words wholly outside the selected segment and omit an empty word only
  when clipping leaves no usable duration;
- keep retained words internally monotonic and reject non-finite timestamps
  through the existing timestamp fallback path;
- preserve word order and rebuild transcription from the resulting words.

PyTorch, MLX, and ONNX follow one order of operations: decode text and words,
align the previous tail with the current prefix, trim the matched current words,
normalize the surviving timestamps to the nominal interval, and rebuild the
segment text. The previous segment keeps the selected copy of a boundary word;
its timestamp has already been clipped to its own nominal end.

Text-only decoders use the same enhanced `stitch_overlapping_text` alignment but
skip word normalization. An overlap window that contributes no surviving words
does not produce an empty exported segment; progress reporting still advances.

### Textual fallback alignment

Extend the tail/prefix alignment for decoders without word timestamps. The
alignment may tolerate a small divergent prefix and short insertions inside the
matched overlap, but it remains constrained to the previous tail and current
prefix. Acceptance requires multiple meaningful matching words and sufficient
coverage; a single short or generic word remains insufficient.

Regression fixtures use the exact two text pairs from issue #33. Existing tests
that protect unrelated short phrases and repeated overlap occurrences remain
mandatory.

### Diarization boundary defense

Speaker mapping remains responsible for splitting a reconciled ASR segment at
real speaker changes. It must additionally enforce its public output invariant:

- consume transcription segments in chronological order;
- never emit a turn starting before the previous emitted turn;
- merge adjacent turns with the same speaker across an ASR chunk boundary when
  their times are contiguous and no real speaker change intervenes;
- do not conceal upstream corruption by blindly stretching or zeroing cues.

The ASR boundary-normalization helper is the primary correction. Mapping checks are defense
in depth and make future contract violations fail in tests instead of reaching
formatters.

### Export path

`TranscriptionProcessor` continues to replace `utterances` with the successful
diarization mapping and passes that one stream to all exporters. No formatter
receives special overlap-repair logic. Markdown, SRT, VTT, and TXT therefore
remain simple renderers of already valid segments.

## Testing strategy

Tests are written before implementation:

- exact issue #33 text pairs are deduplicated by the text-only fallback;
- unrelated short prefixes are retained;
- a boundary word jittered by the two decoders to opposite sides of the cut is
  retained exactly once;
- retained word timestamps are clipped to adjacent nominal intervals;
- timestamped text and word lists remain consistent after reconciliation;
- a deterministic two-chunk MLX fixture that currently maps
  `(0.0–1.0), (1.0–1.5)` to overlapping turns remains monotonic after the fix;
- equivalent PyTorch and ONNX paths use the shared reconciliation behavior;
- speaker mapping handles two adjacent ASR segments and merges the same speaker
  without backward time movement;
- real speaker changes remain distinct;
- Markdown, SRT, and VTT cues are monotonically ordered and non-overlapping;
- existing ASR chunking, token timestamp, diarization mapping, processor, and
  formatter suites remain green;
- the full pytest and Ruff checks pass before packaging.

## Release and packaging

- Set both macOS bundle version fields to `1.3.2`.
- Add `docs/RELEASE_NOTES_1.3.2.md` and a `1.3.2` changelog section describing
  issue #33, affected formats, and the preserved overlap context.
- Run `graphify update .` after source changes.
- Build `dist/GigaAMTranscriber.app` from the repository root with
  `bash packaging/build_exe_mac.sh` and verify it with
  `scripts/verify_macos_bundle.py`.
- Commit the implementation and release metadata on `main`.
- Push `main`, create annotated tag `v1.3.2`, and push the tag to trigger the
  existing `v*` GitHub Actions release workflow.

## Non-goals

- Removing decoder overlap or returning to fixed hard cuts.
- Replacing GigaAM decoding or token timestamp generation.
- Implementing a global decoder lattice in this patch release.
- Changing diarization clustering or speaker-count behavior.
- Repairing invalid segments only inside individual output formatters.

## Success criteria

- The issue #33 repetitions are removed in timestamped and text-only paths.
- No exported segment or diarized turn moves backward or overlaps its successor.
- ASR quality retains overlapping acoustic context at long-form cuts.
- PyTorch, MLX, and ONNX obey one shared boundary-reconciliation contract.
- The verified macOS application exists at `dist/GigaAMTranscriber.app`.
- Pushing `v1.3.2` starts the GitHub Actions release workflow.
