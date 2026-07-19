# Local ASR quality manifest

Audio fixtures are intentionally not committed. Create a JSON array with absolute or repository-relative paths:

```json
[
  {"audio": "corpus/meeting.wav", "reference": "эталонная расшифровка"}
]
```

Use only audio that may legally be processed and stored locally. The release gate compares WER, CER, boundary duplicates, RTFx, and peak RSS from `scripts/benchmark_asr_backends.py`.
