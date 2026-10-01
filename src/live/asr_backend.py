"""Window backend that loads the ASR model lazily on the scheduler thread."""

from __future__ import annotations

import threading

import numpy as np

WARM_UP_SAMPLES = 8_000
"""Half a second of silence: enough for MLX/ONNX to compile their kernels."""


class LazyModelBackend:
    """Load the selected model on first use, off the UI thread.

    One backend is shared by every source of a session, so loading and decoding
    are serialized: mic and system speech arriving together used to start two
    model loads, and the GPU backends are not safe to decode from two threads.
    """

    def __init__(self, model_loader, load_error: str) -> None:
        self._model_loader = model_loader
        self._load_error = load_error
        self._lock = threading.Lock()

    def prepare(self) -> None:
        """Load the model and run one throwaway decode before anyone speaks.

        Otherwise the first phrase of a session waited for the load plus the
        first-decode kernel compilation (about two seconds with MLX, longer
        for CoreML). Raises with diagnostics when the model cannot load; a
        failing throwaway decode is left for a real window to report.
        """
        with self._lock:
            self._ensure_loaded()
            try:
                self._model_loader.transcribe_window(np.zeros(WARM_UP_SAMPLES, dtype=np.float32), 16_000, 0)
            except Exception:
                pass

    def warm_up(self) -> None:
        """`prepare` in the background: the first real window reports failures."""
        try:
            self.prepare()
        except Exception:
            return

    def transcribe_window(self, audio, sample_rate: int, offset_samples: int):
        with self._lock:
            self._ensure_loaded()
            return self._model_loader.transcribe_window(audio, sample_rate, offset_samples)

    def _ensure_loaded(self) -> None:
        if not self._model_loader.is_loaded() and not self._model_loader.load_model():
            detail = self._model_loader.diagnostics().get("error")
            raise RuntimeError(f"{self._load_error}: {detail or 'unknown error'}")
