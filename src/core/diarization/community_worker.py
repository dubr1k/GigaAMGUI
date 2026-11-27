"""Private JSON-lines worker for the isolated pyannote.audio 4.0.7 runtime."""

from __future__ import annotations

import importlib.metadata
import json
import os
import sys
from pathlib import Path

_MODEL_ID = "pyannote/speaker-diarization-community-1"
_EXPECTED_VERSION = "4.0.7"

# Libraries may write to stdout, including from native code. Keep a separate
# protocol descriptor and direct ordinary stdout to the parent's stderr pipe.
_PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
os.dup2(sys.stderr.fileno(), sys.stdout.fileno())


class RuntimeUnavailable(RuntimeError):
    pass


def _send(event: str, **fields) -> None:
    _PROTOCOL.write(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False) + "\n")
    _PROTOCOL.flush()


def _load(request: dict):
    try:
        version = importlib.metadata.version("pyannote.audio")
        if version != _EXPECTED_VERSION:
            raise RuntimeUnavailable(
                f"Установлен pyannote.audio {version}; Community-1 требует {_EXPECTED_VERSION} в отдельном окружении."
            )
        import soundfile
        import torch
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise RuntimeUnavailable(f"Не удалось загрузить зависимости Community-1: {type(exc).__name__}: {exc}") from exc

    local = request.get("model_dir")
    if local:
        snapshot = Path(local).expanduser()
        if not snapshot.is_dir() or not (snapshot / "config.yaml").is_file():
            raise ValueError(f"В каталоге Community-1 нет config.yaml: {snapshot}")
        model = str(snapshot)
    else:
        model = _MODEL_ID
    pipeline = Pipeline.from_pretrained(
        model,
        token=request.get("token") or None,
        cache_dir=request.get("cache_dir") or None,
    )
    if pipeline is None:
        raise RuntimeError(
            "Не удалось загрузить Community-1. Проверьте доступ к модели и read-токен Hugging Face: "
            "https://huggingface.co/pyannote/speaker-diarization-community-1"
        )
    device = request.get("device") or "auto"
    if device == "auto":
        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
    pipeline.to(torch.device(device))
    return pipeline, torch, soundfile


def _diarize(runtime, request: dict):
    if runtime is None:
        raise RuntimeError("Community-1 не подготовлен")
    pipeline, torch, soundfile = runtime
    audio, sample_rate = soundfile.read(request["audio_path"], dtype="float32", always_2d=True)
    waveform = torch.from_numpy(audio.T)

    def hook(_step_name, _artifact, file=None, total=None, completed=None):
        if isinstance(completed, (int, float)):
            _send("progress")

    output = pipeline(
        {"waveform": waveform, "sample_rate": sample_rate},
        hook=hook,
        **request.get("options", {}),
    )
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None or not hasattr(annotation, "itertracks"):
        raise ValueError("Community-1 не вернул exclusive_speaker_diarization")
    return [
        [float(turn.start), float(turn.end), str(speaker)]
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


def main() -> None:
    runtime = None
    token = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Ожидался JSON-объект")
            action = request.get("action")
            if action == "prepare":
                token = request.get("token") or None
                runtime = _load(request)
                _send("result", value=None)
            elif action == "diarize":
                _send("result", value=_diarize(runtime, request))
            else:
                raise ValueError(f"Неизвестная команда Community-1: {action!r}")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if token:
                message = message.replace(token, "[REDACTED]")
            _send("error", message=message, kind="runtime" if isinstance(exc, RuntimeUnavailable) else "model")


if __name__ == "__main__":
    main()
