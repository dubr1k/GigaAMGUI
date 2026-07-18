# Local diarization quality manifest

Audio and RTTM references are intentionally not committed. Create a JSON array:

```json
[
  {"audio": "corpus/meeting.wav", "rttm": "corpus/meeting.rttm", "num_speakers": 3}
]
```

`scripts/benchmark_diarization_backends.py` reports overlap-aware frame DER and JER after optimal speaker-label permutation.
