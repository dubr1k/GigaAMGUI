"""Diarization backend contracts and implementations."""

from .base import DiarizationBackend, SpeakerSegment
from .factory import create_diarization_backend

__all__ = ["DiarizationBackend", "SpeakerSegment", "create_diarization_backend"]
