"""MLX backend adapter for GigaAM RNNT inference."""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable

from ...config import ASR_SEGMENTATION_MODE, ASR_VAD_DEVICE
from .chunking import AudioChunk, plan_audio_chunks, stitch_overlapping_text
from .types import BackendCapabilities, TranscriptionSegment
from .vad import PyannoteVadSegmenter, VadSegmenter, VadUnavailableError, resolve_vad_device


class MLXBackend:
    """ASR backend implemented via ``gigaam_mlx``."""

    name = "mlx"

    def __init__(
        self,
        model: str | None = None,
        *,
        repo: str | None = None,
        segmentation_mode: str | None = None,
        vad_segmenter_factory: Callable[..., VadSegmenter] | None = None,
    ):
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
        self._vad_segmenter_factory = vad_segmenter_factory or PyannoteVadSegmenter
        self._vad_segmenter: VadSegmenter | None = None
        self._vad_segmenter_key: tuple[bytes, str] | None = None
        self._vad_failure_key: tuple[bytes, str] | None = None
        self.segmentation_strategy = segmentation_mode or ASR_SEGMENTATION_MODE
        if self.segmentation_strategy not in {"vad", "overlap_chunks", "fixed_chunks"}:
            raise ValueError(f"Неизвестный режим сегментации: {self.segmentation_strategy}")
        self.segmentation_mode = "not_run"
        self.segmentation_fallback_reason: str | None = None
        self._logger: Callable[[str], None] | None = None

    def load(self, logger: Callable[[str], None] | None = None) -> bool:
        self._logger = logger
        if self.is_loaded():
            return True
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

    def _fixed_chunks(self, audio) -> list[dict]:
        gm = self._gigaam_mlx
        if gm is None:
            raise RuntimeError("MLX backend is not initialized")
        return gm.audio.split_audio(audio)

    def _legacy_chunks(self, audio) -> list[AudioChunk]:
        return [
            AudioChunk(
                group=index,
                decode_start_sample=int(chunk["start_sample"]),
                decode_end_sample=int(chunk["end_sample"]),
                start_sec=float(chunk["start_sec"]),
                end_sec=float(chunk["end_sec"]),
            )
            for index, chunk in enumerate(self._fixed_chunks(audio))
        ]

    def _chunks_from_vad_boundaries(
        self,
        audio,
        boundaries: list[tuple[float, float]],
    ) -> list[AudioChunk]:
        gm = self._gigaam_mlx
        if gm is None:
            raise RuntimeError("MLX backend is not initialized")

        return plan_audio_chunks(
            audio,
            boundaries,
            sample_rate=gm.audio.SAMPLE_RATE,
            max_chunk_seconds=20.0,
        )

    def _use_fixed_chunks(self, audio, reason: str) -> list[AudioChunk]:
        self.segmentation_mode = "fixed_chunks"
        self.segmentation_fallback_reason = reason
        if self._logger is not None:
            self._logger(f"ПРЕДУПРЕЖДЕНИЕ: {reason}")
        return self._legacy_chunks(audio)

    def _use_overlap_chunks(self, audio, reason: str) -> list[AudioChunk]:
        gm = self._gigaam_mlx
        if gm is None:
            raise RuntimeError("MLX backend is not initialized")
        self.segmentation_mode = "overlap_chunks"
        self.segmentation_fallback_reason = reason
        if self._logger is not None:
            self._logger(f"ПРЕДУПРЕЖДЕНИЕ: {reason}")
        total_seconds = float(len(audio)) / gm.audio.SAMPLE_RATE if len(audio) else 0.0
        return plan_audio_chunks(
            audio,
            [(0.0, total_seconds)],
            sample_rate=gm.audio.SAMPLE_RATE,
            max_chunk_seconds=20.0,
        )

    def _vad_context(self) -> tuple[str | None, tuple[bytes, str]]:
        hf_token = os.getenv("HF_TOKEN", "").strip() or None
        token_fingerprint = hashlib.sha256((hf_token or "").encode()).digest()
        return hf_token, (token_fingerprint, resolve_vad_device(ASR_VAD_DEVICE))

    def _get_vad_segmenter(
        self,
        *,
        token: str | None,
        segmenter_key: tuple[bytes, str],
    ) -> VadSegmenter:
        if self._vad_failure_key == segmenter_key:
            raise VadUnavailableError("previous VAD initialization failed")
        if self._vad_segmenter is None or self._vad_segmenter_key != segmenter_key:
            self._vad_segmenter = self._vad_segmenter_factory(
                token=token,
                device=segmenter_key[1],
            )
            self._vad_segmenter_key = segmenter_key
            self._vad_failure_key = None
        return self._vad_segmenter

    @staticmethod
    def _vad_fallback_reason(exc: Exception) -> str:
        recovery_hint = ""
        if isinstance(exc, VadUnavailableError):
            recovery_hint = (
                "проверьте локальный кэш или HF_TOKEN и доступ к "
                "pyannote/segmentation-3.0; "
            )
        return (
            f"VAD недоступен ({type(exc).__name__}): "
            f"{recovery_hint}использовано резервное разбиение MLX "
            "по тихим точкам с перекрытием"
        )

    def _resolve_chunks(
        self,
        audio_path: str,
        audio,
        *,
        total_seconds: float,
    ) -> list[AudioChunk]:
        if self.segmentation_strategy == "fixed_chunks":
            return self._use_fixed_chunks(
                audio,
                "VAD отключён настройкой ASR_SEGMENTATION_MODE: "
                "использовано legacy-разбиение MLX до 20 секунд без перекрытия",
            )
        if self.segmentation_strategy == "overlap_chunks":
            return self._use_overlap_chunks(
                audio,
                "VAD отключён настройкой ASR_SEGMENTATION_MODE: "
                "использовано разбиение MLX по тихим точкам с перекрытием",
            )

        hf_token, segmenter_key = self._vad_context()
        try:
            segmenter = self._get_vad_segmenter(
                token=hf_token,
                segmenter_key=segmenter_key,
            )
            boundaries = segmenter.segment_file(
                audio_path,
                audio_duration=total_seconds,
            )
        except Exception as exc:
            self._vad_segmenter = None
            self._vad_segmenter_key = None
            self._vad_failure_key = (
                segmenter_key if isinstance(exc, VadUnavailableError) else None
            )
            return self._use_overlap_chunks(
                audio,
                self._vad_fallback_reason(exc),
            )

        self.segmentation_mode = "vad"
        self.segmentation_fallback_reason = None
        chunks = self._chunks_from_vad_boundaries(audio, boundaries)
        if self._logger is not None:
            self._logger(
                "ASR сегментация: VAD, "
                f"речевых областей: {len(boundaries)}, окон декодера: {len(chunks)}"
            )
        return chunks

    def _transcribe_in_chunks(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
    ) -> list[dict]:
        gm = self._gigaam_mlx
        if gm is None:
            raise RuntimeError("MLX backend is not initialized")

        audio = gm.load_audio(audio_path)
        total_samples = len(audio)
        sample_rate = gm.audio.SAMPLE_RATE
        total_seconds = float(total_samples) / sample_rate if total_samples else 0.0
        chunks = self._resolve_chunks(
            audio_path,
            audio,
            total_seconds=total_seconds,
        )

        result_segments: list[dict] = []
        previous_result_index: int | None = None
        previous_group: int | None = None
        reported = 0.0
        for chunk in chunks:
            start_sample = chunk.decode_start_sample
            end_sample = chunk.decode_end_sample
            chunk_audio = audio[start_sample:end_sample]
            mel = gm.audio.compute_mel(chunk_audio)

            mx = __import__("mlx.core", fromlist=["array"])  # lazy import
            mel_mx = mx.array(mel[None, :])

            encoded, seq_len = self.model.encode(mel_mx)  # type: ignore[union-attr]
            mx.eval(encoded)
            token_ids = self.model.decode(encoded, seq_len)  # type: ignore[union-attr]
            text = self.tokenizer.decode(token_ids) if self.tokenizer is not None else ""

            text = str(text).strip()
            if text:
                if (
                    chunk.overlaps_previous
                    and previous_result_index is not None
                    and previous_group == chunk.group
                ):
                    previous_text = result_segments[previous_result_index]["text"]
                    previous_text, text, _overlap = stitch_overlapping_text(
                        previous_text,
                        text,
                    )
                    result_segments[previous_result_index]["text"] = previous_text
                    if not text and _overlap:
                        result_segments[previous_result_index]["end"] = float(
                            chunk.end_sec
                        )
                if text:
                    result_segments.append({
                        "start": float(chunk.start_sec),
                        "end": float(chunk.end_sec),
                        "text": text,
                    })
                    previous_result_index = len(result_segments) - 1
                    previous_group = chunk.group
            else:
                previous_result_index = None
                previous_group = None

            if progress_callback is not None and total_samples > 0:
                processed = min(float(chunk.end_sec) / total_seconds, 1.0)
                if processed >= reported:
                    progress_callback(
                        min(processed, 1.0),
                        min(float(chunk.end_sec), total_seconds),
                        total_seconds,
                    )
                    reported = processed

        if progress_callback is not None and total_samples > 0 and reported < 1.0:
            progress_callback(1.0, total_seconds, total_seconds)

        return result_segments

    def unload(self) -> None:
        with self._lock:
            self.model = None
            self.tokenizer = None
            self._vad_segmenter = None
            self._vad_segmenter_key = None
            self._vad_failure_key = None
            self.segmentation_mode = "not_run"
            self.segmentation_fallback_reason = None
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
            segmentation_mode=self.segmentation_mode,
            segmentation_fallback_reason=self.segmentation_fallback_reason,
        )

    def __repr__(self) -> str:
        return (
            f"MLXBackend(model={self.model_name!r}, repo_id={self.repo_id!r}, "
            f"loaded={self.is_loaded()})"
        )
