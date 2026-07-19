# Local diarization quality manifest

Audio and RTTM references are intentionally not committed. Create a JSON array:

```json
[
  {"audio": "corpus/meeting.wav", "rttm": "corpus/meeting.rttm", "num_speakers": 3}
]
```

`scripts/benchmark_diarization_backends.py` reports DER (with the
miss / false-alarm / confusion breakdown), JER and the speaker-count error via
`pyannote.metrics`. Use `--collar 0.25` to forgive boundary jitter the way DIHARD
does, and `--skip-overlap` to exclude overlapping speech.

## Bootstrapping a reference

Hand-annotating RTTM is expensive. Entries without an `rttm` key are accepted
when `--dump-rttm DIR` is given, which writes each backend's hypothesis as RTTM:

```bash
PYTHONPATH=. python scripts/benchmark_diarization_backends.py manifest.json \
    --backend pyannote --dump-rttm rttm/ --output ref.json
```

The result is a *pseudo*-reference, not ground truth. Check how far two
independent engines disagree before trusting it: on a 5-minute two-voice
interview pyannote and Sortformer differ by DER 0.070 (collar 0.25), so
differences below roughly that level say nothing.

## Sweeping the clustering threshold

`--threshold` may be repeated; it only applies to the ONNX backend and is
injected through the `cluster_fn` seam, so production defaults stay untouched.

```bash
PYTHONPATH=. python scripts/benchmark_diarization_backends.py manifest.json \
    --backend onnx --ignore-manifest-speakers \
    --threshold 0.35 --threshold 0.6 --threshold 0.8 \
    --collar 0.25 --output sweep.json
```

Measured on one 5-minute two-voice interview against a pyannote pseudo-reference
(single file — indicative, not conclusive):

| Mode | Speakers | DER | Confusion |
|---|---|---|---|
| auto, threshold 0.35 | 5 | 0.111 | 0.046 |
| auto, threshold 0.5 | 4 | 0.103 | 0.046 |
| auto, threshold 0.6–0.8 | 3 | 0.103 | 0.046 |
| `num_speakers=2` | 2 | 0.152 | 0.141 |

Two things follow. Raising the threshold past 0.6 changes nothing, so the
speaker over-count is bounded by the cannot-link constraint, not by the
threshold. And forcing the speaker count fixes the count while making the
assignment worse — the relaxed cannot-link merges the wrong pair. Neither
conclusion should be acted on until it reproduces across a real corpus.
