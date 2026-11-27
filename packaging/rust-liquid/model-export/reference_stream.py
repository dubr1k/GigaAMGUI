"""Emit the official pinned PyTorch streaming e2e text and word timings.

Reference-only development tool. It is not used by the Rust application.
The matching model snapshot must already be present in the Hugging Face cache.
Input must be 16 kHz, mono, signed PCM16 WAVE, like the Rust proof CLI.
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import torch
from transformers import AutoModel

MODELS = {
    "v4": ("ai-sage/GigaAM-v4", "9fe3ebf50c9e0cce5eed60d546938632238b6a96"),
    "multilingual": (
        "ai-sage/GigaAM-Multilingual-v2",
        "c21483b5e185ff32f6fb2e2c801f92f4e7fbfee8",
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=MODELS)
    parser.add_argument("wave", type=Path)
    parser.add_argument("packet_samples", type=int, nargs="?", default=1600)
    args = parser.parse_args()
    if not 1 <= args.packet_samples <= 160_000:
        parser.error("packet_samples must be between 1 and 160000")
    repo, revision = MODELS[args.model]
    model = AutoModel.from_pretrained(
        repo,
        revision=revision,
        local_files_only=True,
        trust_remote_code=True,
        torch_dtype=torch.float32,
    ).eval()
    stream = model.create_stream(future_context_ms=320, word_timestamps=True)
    text = []
    words = []

    def append(results):
        result = results["e2e"]
        print("COMMIT " + json.dumps(result.text, ensure_ascii=False), file=sys.stderr)
        for word in result.words or []:
            print(f"WORD {word.text} {word.start:.3f} {word.end:.3f}", file=sys.stderr)
        text.append(result.text)
        words.extend(result.words or [])

    with wave.open(str(args.wave), "rb") as source, torch.inference_mode():
        if source.getnchannels() != 1 or source.getframerate() != 16_000 or source.getsampwidth() != 2 or source.getcomptype() != "NONE":
            raise ValueError("expected 16k mono PCM16 WAVE")
        while data := source.readframes(args.packet_samples):
            if len(data) % 2:
                raise ValueError("truncated PCM16 sample")
            audio = torch.frombuffer(bytearray(data), dtype=torch.int16).to(torch.float32).div_(32768.0)
            append(stream.transcribe_partial(audio))
        append(stream.flush())

    print("".join(text))
    for word in words:
        print(f"{word.text} {word.start:.3f} {word.end:.3f}")


if __name__ == "__main__":
    main()
