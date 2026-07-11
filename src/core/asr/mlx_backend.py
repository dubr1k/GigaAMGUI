"""MLX backend adapter for GigaAM RNNT inference."""

from __future__ import annotations

import threading
from collections.abc import Callable

from .types import BackendCapabilities, TranscriptionSegment


class MLXBackend:
    """ASR backend implemented via ``gigaam_mlx``."""

    name = "mlx"

    def __init__(self, model: str | None = None, *, repo: str | None = None):
        requested_model = (model or "rnnt").strip().lower()
        model_aliases = {
            "e2e_rnnt": "rnnt",
            "v3_e2e_rnnt": "rnnt",
        }
        self.model_name = model_aliases.get(requested_model, requested_model)
        self.repo_id = repo or "aystream/GigaAM-v3-e2e-rnnt-mlx"
        self.model = None
        self.tokenizer = None
        self.device = "mps"
        self._lock = threading.Lock()
        self._gigaam_mlx = None

    def load(self, logger: Callable[[str], None] | None = None) -> bool:
        try:
            import gigaam_mlx

            self._gigaam_mlx = gigaam_mlx
            if logger:
                logger(f"MLX backend load requested: repo={self.repo_id}")

            model, tokenizer = gigaam_mlx.load_model(
                model_type=self.model_name,
                repo_id=self.repo_id,
            )
            self.model = model
            self.tokenizer = tokenizer
            return True
        except Exception as exc:
            if logger:
                logger(
                    f"MLX load failed: backend={self.name}, model={self.model_name}, "
                    f"repo={self.repo_id}: {type(exc).__name__}: {exc}"
                )
            return False

    def transcribe_longform(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
    ) -> list[TranscriptionSegment]:
        if self.model is None:
            raise RuntimeError("MLX модель не загружена")

        with self._lock:
            try:
                if self._gigaam_mlx is None:
                    raise RuntimeError("MLX backend is not initialized")

                raw_segments = self._transcribe_in_chunks(
                    audio_path,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"MLX transcription failed: backend={self.name}, model={self.model_name}, repo={self.repo_id}: {type(exc).__name__}: {exc}"
                ) from exc

        segments: list[TranscriptionSegment] = []
        for item in raw_segments or []:
            text = str((item or {}).get("text", "")).strip()
            if not text:
                continue

            raw_start = float((item or {}).get("start", 0.0))
            raw_end = float((item or {}).get("end", raw_start))
            start = max(0.0, raw_start)
            end = max(start, raw_end)
            if end < start:
                end = start

            segments.append(
                {
                    "transcription": text,
                    "boundaries": (start, end),
                }
            )

        return segments

    def _transcribe_in_chunks(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
    ) -> list[dict]:
        gm = self._gigaam_mlx
        if gm is None:
            raise RuntimeError("MLX backend is not initialized")

        audio = gm.load_audio(audio_path)
        chunks = gm.audio.split_audio(audio)
        total_samples = len(audio)
        total_seconds = float(total_samples) / gm.audio.SAMPLE_RATE if total_samples else None

        result_segments: list[dict] = []
        for chunk in chunks:
            chunk_audio = audio[chunk["start_sample"]:chunk["end_sample"]]
            mel = gm.audio.compute_mel(chunk_audio)

            mx = __import__("mlx.core", fromlist=["array"])  # lazy import
            mel_mx = mx.array(mel[None, :])

            encoded, seq_len = self.model.encode(mel_mx)  # type: ignore[union-attr]
            mx.eval(encoded)
            token_ids = self.model.decode(encoded, seq_len)  # type: ignore[union-attr]
            text = self.tokenizer.decode(token_ids) if self.tokenizer is not None else ""

            result_segments.append({
                "start": float(chunk["start_sec"]),
                "end": float(chunk["end_sec"]),
                "text": text,
            })

            if progress_callback is not None and total_samples > 0 and total_seconds is not None:
                processed = float(chunk["end_sample"]) / total_samples
                progress_callback(min(processed, 1.0), chunk["end_sec"], total_seconds)

        return result_segments

    def unload(self) -> None:
        self.model = None
        self.tokenizer = None
        try:
            import mlx

            clear_cache = getattr(mlx, "clear_cache", None)
            if callable(clear_cache):
                clear_cache()
        except Exception:
            pass

    def is_loaded(self) -> bool:
        return self.model is not None and self.tokenizer is not None

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend=self.name,
            model=self.repo_id,
            device=self.device,
        )

    def __repr__(self) -> str:
        return (
            f"MLXBackend(model={self.model_name!r}, repo_id={self.repo_id!r}, "
            f"loaded={self.is_loaded()})"
        )
