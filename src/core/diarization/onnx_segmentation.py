"""ONNX powerset speaker segmentation without collapsing overlap to VAD."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..asr.onnx_provider import (
    available_onnx_providers,
    onnx_session_providers,
    resolve_onnx_providers,
)


@dataclass(frozen=True)
class SegmentationResult:
    probabilities: np.ndarray
    window_starts: np.ndarray
    frame_step: float
    audio_duration: float


class OnnxSegmentation:
    """Run pyannote powerset segmentation in overlapping ten-second windows."""

    WINDOW_SECONDS = 10.0
    OVERLAP_SECONDS = 5.0
    FRAME_STRIDE_SAMPLES = 270

    def __init__(
        self,
        *,
        provider: str = "auto",
        model_dir: str | None = None,
        sample_rate: int = 16_000,
        session: Any | None = None,
    ) -> None:
        self.provider = provider
        self.model_dir = model_dir
        self.sample_rate = sample_rate
        self._session = session
        self._vad_owner: Any | None = None

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        import onnx_asr  # noqa: PLC0415

        selection = resolve_onnx_providers(
            self.provider,
            available=available_onnx_providers(),
        )
        vad = onnx_asr.load_vad(
            "onnx-community/pyannote-segmentation-3.0",
            path=self.model_dir,
            providers=onnx_session_providers(selection),
        )
        session = getattr(vad, "_model", None)
        if session is None or not callable(getattr(session, "run", None)):
            raise RuntimeError(
                "onnx-asr 0.12.0 PyAnnote adapter does not expose its pinned ORT session"
            )
        self._vad_owner = vad
        self._session = session
        return session

    @staticmethod
    def _expand_powerset(probabilities: np.ndarray) -> np.ndarray:
        if probabilities.ndim != 2 or probabilities.shape[1] != 7:
            raise ValueError(
                f"Ожидались pyannote powerset probabilities [frames, 7], получено {probabilities.shape}"
            )
        speakers = np.empty((probabilities.shape[0], 3), dtype=np.float32)
        speakers[:, 0] = probabilities[:, 1] + probabilities[:, 4] + probabilities[:, 5]
        speakers[:, 1] = probabilities[:, 2] + probabilities[:, 4] + probabilities[:, 6]
        speakers[:, 2] = probabilities[:, 3] + probabilities[:, 5] + probabilities[:, 6]
        return speakers

    def infer(self, waveform: np.ndarray) -> SegmentationResult:
        audio = np.asarray(waveform, dtype=np.float32).reshape(-1)
        window_size = int(round(self.WINDOW_SECONDS * self.sample_rate))
        step_size = int(round((self.WINDOW_SECONDS - self.OVERLAP_SECONDS) * self.sample_rate))
        stop = max(1, len(audio) - int(round(self.OVERLAP_SECONDS * self.sample_rate)))
        starts = list(range(0, stop, step_size)) or [0]
        session = self._ensure_session()
        windows = []
        for start in starts:
            window = audio[start:start + window_size]
            if len(window) < window_size:
                window = np.pad(window, (0, window_size - len(window)))
            logits = session.run(["logits"], {"input_values": window[None, None, :]})[0]
            probabilities = np.exp(np.asarray(logits, dtype=np.float32)[0])
            windows.append(self._expand_powerset(probabilities))
        return SegmentationResult(
            probabilities=np.stack(windows),
            window_starts=np.asarray(starts, dtype=np.float64) / self.sample_rate,
            frame_step=self.FRAME_STRIDE_SAMPLES / self.sample_rate,
            audio_duration=len(audio) / self.sample_rate,
        )

    def unload(self) -> None:
        self._session = None
        self._vad_owner = None
