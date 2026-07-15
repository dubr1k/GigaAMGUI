"""ASR backend protocol shared by PyTorch and MLX implementations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .types import BackendCapabilities, TranscriptionSegment


class ASRBackend(Protocol):
    """Contract for speech backends."""

    name: str

    def load(self, logger: Callable[[str], None] | None = None) -> bool:
        """Load backend resources lazily."""

    def transcribe_longform(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
    ) -> list[TranscriptionSegment]:
        """Transcribe long audio with backend-aware speech segmentation."""

    def unload(self) -> None:
        """Release backend resources."""

    def is_loaded(self) -> bool:
        """Return True when inference resources are ready."""

    def capabilities(self) -> BackendCapabilities:
        """Current runtime capabilities."""
