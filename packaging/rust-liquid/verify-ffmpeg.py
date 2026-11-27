#!/usr/bin/env python3
"""Exercise the packaged decoder command on native CI hosts with small fixtures."""
import math
import pathlib
import struct
import subprocess
import sys

binary = pathlib.Path(sys.argv[1]).resolve(strict=True)
fixtures = pathlib.Path(sys.argv[2]).resolve(strict=True)
names = (
    "clip.wav", "clip.mp3", "clip.m4a", "clip.aac", "clip.mp4", "clip.mov",
    "clip.qta", "clip.3gp", "clip.avi", "clip.mkv", "clip.webm", "clip.ogg",
    "clip.opus", "clip.wma", "clip.flac", "video.mp4",
)
for name in names:
    result = subprocess.run(
        [str(binary), "-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-xerror",
         "-copyts", "-start_at_zero", "-err_detect", "explode",
         "-protocol_whitelist", "file,pipe", "-i", str(fixtures / name),
         "-map", "0:a:0", "-vn", "-sn", "-dn",
         "-af", "aresample=16000:async=1:first_pts=0:min_hard_comp=0:min_comp=0",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"],
        stdin=subprocess.DEVNULL, capture_output=True,
        check=False, timeout=30,
    )
    if result.returncode or len(result.stdout) % 4:
        raise RuntimeError(f"{name}: decoder exit {result.returncode}: {result.stderr.decode(errors='replace')}")
    samples = [sample[0] for sample in struct.iter_unpack("<f", result.stdout)]
    if not 13_000 <= len(samples) <= 19_000 or not all(map(math.isfinite, samples)):
        raise RuntimeError(f"{name}: unexpected output length or non-finite sample")
    if not any(abs(sample) > 0.005 for sample in samples):
        raise RuntimeError(f"{name}: decoded audio is silent")
    print(f"{name}: {len(samples)} finite mono samples")
