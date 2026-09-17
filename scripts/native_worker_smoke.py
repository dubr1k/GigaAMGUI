"""Прогнать один реальный файл через JSONL-worker замороженного companion.

``ping``/``pong`` доказывает только, что бинарник стартует. Транскрибация
ломается глубже: колесо scipy, которое dyld новой macOS отказывается грузить,
или ``auto``-backend, который в офлайн-раскладке тянется за MLX-моделью, — ни то,
ни другое импортом модулей не поймать. Скрипт делает то же, что GigaAMLiquid:
держит stdin открытым, шлёт ``start`` и ждёт ``completed`` (worker завершает
фоновую задачу только пока stdin жив).

Использование::

    python scripts/native_worker_smoke.py \\
        stage/.../GigaAMTranscriber.app/Contents/MacOS/GigaAMTranscriber \\
        clip.wav out_dir [--backend auto] [--timeout 600]

Код возврата 0 только при ``completed.success == true`` и непустом файле
результата. Окружение (``HF_HOME``, ``HF_HUB_OFFLINE`` …) наследуется как есть,
чтобы вызывающая сторона могла воспроизвести раскладку релиза.

``--live`` гоняет тот же клип через live-протокол (``live_start`` →
``live_audio`` чанками по 100 мс → ``live_stop``), как это делает страница Live
в GigaAMLiquid, и требует хотя бы одного ``live_final`` и непустого
``transcript.txt`` в папке сессии.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import wave
from array import array
from collections.abc import Iterator, Sequence
from pathlib import Path


def run_smoke(
    companion: Path,
    clip: Path,
    output_dir: Path,
    *,
    backend: str = "auto",
    timeout: float = 600.0,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = {
        "type": "start",
        "files": [str(clip)],
        "output_dir": str(output_dir),
        "formats": ["txt"],
        "backend": backend,
        "model": "v3_e2e_rnnt",
        "onnx_provider": "auto",
        "diarization": False,
    }
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    proc = subprocess.Popen(
        [str(companion), "--native-worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        cwd=companion.parent,
        env=env,
        text=True,
        bufsize=1,
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(command) + "\n")
    proc.stdin.flush()

    completed: dict | None = None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print(f"[worker] {line}", flush=True)
                continue
            kind = message.get("type")
            if kind == "log":
                print(f"[worker] {message.get('message', '')}", flush=True)
            elif kind == "error":
                print(f"[worker] ERROR: {message.get('message', '')}", flush=True)
            elif kind == "completed":
                completed = message
                break
            if time.monotonic() - started > timeout:
                print(f"native worker smoke: timeout after {timeout:.0f}s", flush=True)
                break
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()

    if completed is None:
        print("native worker smoke: worker exited without `completed`", flush=True)
        return 1
    if not completed.get("success"):
        print(f"native worker smoke: completed.success is false: {completed}", flush=True)
        return 1
    result = output_dir / f"{clip.stem}.txt"
    if not result.is_file():
        print(f"native worker smoke: result file missing: {result}", flush=True)
        return 1
    text = result.read_text(encoding="utf-8", errors="replace").strip()
    elapsed = time.monotonic() - started
    print(f"native worker smoke: OK in {elapsed:.1f}s, backend={backend}: {text!r}", flush=True)
    return 0


def pcm_chunks(
    samples: Sequence[int],
    sample_rate: int,
    chunk_seconds: float = 0.1,
    *,
    first_seq: int = 0,
    first_offset: int = 0,
) -> Iterator[dict]:
    """Yield ``live_audio`` payloads from int16 mono samples, 100 ms each.

    Pure Python on purpose: the Swift CI job runs this with the runner's
    system python3, which has no numpy.
    """
    frames_per_chunk = int(sample_rate * chunk_seconds)
    for index, start in enumerate(range(0, len(samples), frames_per_chunk)):
        block = array("h", samples[start:start + frames_per_chunk])
        if sys.byteorder != "little":
            block.byteswap()
        offset = first_offset + start
        yield {
            "type": "live_audio",
            "source": "mic",
            "seq": first_seq + index,
            "sample_offset": offset,
            "timestamp_ns": int(offset / sample_rate * 1_000_000_000),
            "pcm": base64.b64encode(block.tobytes()).decode("ascii"),
        }


def _load_wav_16k_mono(path: Path) -> array:
    """Read a 16-bit PCM WAV as int16 mono at 16 kHz (linear resampling)."""
    with wave.open(str(path), "rb") as handle:
        rate, channels, width = handle.getframerate(), handle.getnchannels(), handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise SystemExit("live smoke expects a 16-bit PCM WAV")
    interleaved = array("h")
    interleaved.frombytes(raw)
    if sys.byteorder != "little":
        interleaved.byteswap()
    if channels > 1:
        mono = array("h", (
            sum(interleaved[i:i + channels]) // channels for i in range(0, len(interleaved), channels)
        ))
    else:
        mono = interleaved
    if rate == 16_000:
        return mono
    step = rate / 16_000
    total = int(len(mono) / step)
    resampled = array("h")
    for index in range(total):
        position = index * step
        left = int(position)
        right = min(left + 1, len(mono) - 1)
        weight = position - left
        resampled.append(int(mono[left] * (1 - weight) + mono[right] * weight))
    return resampled


def run_live_smoke(
    companion: Path,
    clip: Path,
    session_root: Path,
    *,
    backend: str = "auto",
    timeout: float = 600.0,
    worker_args: tuple[str, ...] = ("--native-worker",),
) -> int:
    session_root.mkdir(parents=True, exist_ok=True)
    samples = _load_wav_16k_mono(clip)
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    proc = subprocess.Popen(
        [str(companion), *worker_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        cwd=companion.parent,
        env=env,
        text=True,
        bufsize=1,
    )
    assert proc.stdin is not None and proc.stdout is not None

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    send({
        "type": "live_start", "session_root": str(session_root), "sources": ["mic"], "sample_rate": 16_000,
        "diarization_mode": "off", "exports": {"txt": True},
        "backend": backend, "model": "v3_e2e_rnnt", "onnx_provider": "auto",
    })
    sent = 0
    for chunk in pcm_chunks(samples, 16_000):
        send(chunk)
        sent += 1
    # Trailing silence lets the scheduler finalize the last utterance before stop.
    silence = array("h", bytes(16_000 * 4 * 2))
    for chunk in pcm_chunks(silence, 16_000, first_seq=sent, first_offset=len(samples)):
        send(chunk)
    send({"type": "live_stop"})

    finals: list[str] = []
    stopped: dict | None = None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print(f"[worker] {line}", flush=True)
                continue
            kind = message.get("type")
            if kind == "log":
                print(f"[worker] {message.get('message', '')}", flush=True)
            elif kind == "error":
                print(f"[worker] ERROR: {message.get('message', '')}", flush=True)
            elif kind == "live_final":
                finals.append(str(message.get("text", "")))
            elif kind == "live_stopped":
                stopped = message
                break
            if time.monotonic() - started > timeout:
                print(f"live smoke: timeout after {timeout:.0f}s", flush=True)
                break
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()

    if stopped is None:
        print("live smoke: worker exited without `live_stopped`", flush=True)
        return 1
    if not finals:
        print("live smoke: no `live_final` events", flush=True)
        return 1
    transcript = next((Path(p) for p in stopped.get("saved_files", []) if p.endswith("transcript.txt")), None)
    if transcript is None or not transcript.is_file() or not transcript.read_text(encoding="utf-8").strip():
        print(f"live smoke: transcript.txt missing or empty: {stopped}", flush=True)
        return 1
    elapsed = time.monotonic() - started
    print(f"live smoke: OK in {elapsed:.1f}s, backend={backend}, finals={finals!r}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("companion", type=Path, help="исполняемый файл companion (Contents/MacOS/GigaAMTranscriber)")
    parser.add_argument("clip", type=Path, help="аудиофайл для транскрибации")
    parser.add_argument("output_dir", type=Path, help="куда писать результат (для --live: корень сессий)")
    parser.add_argument("--backend", default="auto", help="ASR backend, как его передаёт GigaAMLiquid (по умолчанию auto)")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--live", action="store_true", help="прогнать клип через live_start/live_audio/live_stop")
    parser.add_argument(
        "--worker-arg", action="append", default=None,
        help="аргумент запуска worker'а вместо --native-worker (можно повторять; пустое значение — без аргументов)",
    )
    args = parser.parse_args(argv)
    if not os.access(args.companion, os.X_OK):
        parser.error(f"companion не исполняемый: {args.companion}")
    if not args.clip.is_file():
        parser.error(f"нет аудиофайла: {args.clip}")
    if args.live:
        worker_args = ("--native-worker",) if args.worker_arg is None else tuple(a for a in args.worker_arg if a)
        return run_live_smoke(
            args.companion.resolve(),
            args.clip.resolve(),
            args.output_dir.resolve(),
            backend=args.backend,
            timeout=args.timeout,
            worker_args=worker_args,
        )
    return run_smoke(
        args.companion.resolve(),
        args.clip.resolve(),
        args.output_dir.resolve(),
        backend=args.backend,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
