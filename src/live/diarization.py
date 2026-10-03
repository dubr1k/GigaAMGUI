"""Live diarization policy helpers without cross-session identity state."""

from __future__ import annotations

from .types import DiarizationMode, TranscriptEvent

LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS = 10
LIVE_ESTIMATE_BACKEND = "sortformer"
"""The only backend family designed for streaming speaker estimates."""


class BuiltinDiarizers:
    """Diarization backends shipped with the app, created on request.

    None of them can estimate speakers while recording: pyannote, the ONNX
    chain and Sortformer (NeMo or ONNX) all diarize a finished file and have
    no `estimate_events`. Sessions read `supports_live_estimate` before
    asking for a live estimator, so choosing "Live estimate" no longer loads
    a Sortformer model per source only to report it unavailable.
    """

    supports_live_estimate = False

    def __call__(self, backend: str):
        from src.core.diarization.factory import create_diarization_backend

        return create_diarization_backend(backend)


def label_event(event: TranscriptEvent, mode: DiarizationMode) -> TranscriptEvent:
    """Keep source labels stable when diarization is disabled."""
    if mode is DiarizationMode.OFF:
        return event
    return event
