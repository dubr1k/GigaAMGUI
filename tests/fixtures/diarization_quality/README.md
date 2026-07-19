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

Measured on two segments of the same two-voice interview against a pyannote
pseudo-reference, collar 0.25. On both, pyannote and Sortformer independently
agree on 2 speakers, and they disagree with each other by DER 0.070 (5 min) and
0.067 (15 min) — that is the noise floor below which nothing here means anything.

| Mode | 5 min: speakers / DER | 15 min: speakers / DER |
|---|---|---|
| auto, threshold 0.35 | 5 / 0.111 | 9 / 0.128 |
| auto, threshold 0.5 | 4 / 0.103 | 3 / 0.117 |
| auto, threshold 0.6 and above | 3 / 0.103 | 3 / 0.117 |
| `num_speakers=2` | 2 / 0.152 | 2 / 0.059 |

One conclusion replicates: raising the threshold past ~0.5–0.6 changes nothing
at all, so the speaker over-count is bounded by the cannot-link constraint, not
by the threshold. Tuning the threshold further is wasted effort.

The other does not. On the 5-minute segment forcing the speaker count made the
assignment much worse (0.152 against 0.103); on the 15-minute one it was by far
the best result measured (0.059 — better than the two reference engines agree
with each other). Whether the forced merge picks the right pair evidently
depends on the material, and a single file will happily support either
conclusion. That is the whole argument for building a corpus before touching
`clustering.py`.
