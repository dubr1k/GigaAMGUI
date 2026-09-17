"""Window backend that loads the ASR model lazily on the scheduler thread."""

from __future__ import annotations


class LazyModelBackend:
    """Load the selected model on first use, off the UI thread."""

    def __init__(self, model_loader, load_error: str) -> None:
        self._model_loader = model_loader
        self._load_error = load_error

    def transcribe_window(self, audio, sample_rate: int, offset_samples: int):
        if not self._model_loader.is_loaded() and not self._model_loader.load_model():
            detail = self._model_loader.diagnostics().get("error")
            raise RuntimeError(f"{self._load_error}: {detail or 'unknown error'}")
        return self._model_loader.transcribe_window(audio, sample_rate, offset_samples)
