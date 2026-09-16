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
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("companion", type=Path, help="исполняемый файл companion (Contents/MacOS/GigaAMTranscriber)")
    parser.add_argument("clip", type=Path, help="аудиофайл для транскрибации")
    parser.add_argument("output_dir", type=Path, help="куда писать результат")
    parser.add_argument("--backend", default="auto", help="ASR backend, как его передаёт GigaAMLiquid (по умолчанию auto)")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    if not os.access(args.companion, os.X_OK):
        parser.error(f"companion не исполняемый: {args.companion}")
    if not args.clip.is_file():
        parser.error(f"нет аудиофайла: {args.clip}")
    return run_smoke(
        args.companion.resolve(),
        args.clip.resolve(),
        args.output_dir.resolve(),
        backend=args.backend,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
