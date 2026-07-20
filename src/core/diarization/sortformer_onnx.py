"""NVIDIA Streaming Sortformer v2.1 inference through ONNX Runtime.

The ONNX streaming state and cache selection follow the public NeMo model and
the MIT-licensed parakeet-rs implementation by Enes Altun.  Unlike NeMo, this
module has no PyTorch dependency and therefore works in the Windows portable
build.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from ...utils.model_cache import resolve_bundled_snapshot
from ...utils.runtime_manager import hf_cache_dir
from ..asr.onnx_provider import (
    available_onnx_providers,
    onnx_session_providers,
    resolve_onnx_providers,
)
from ..model_preparation import PreparationCancelled, PreparationState

SORTFORMER_ONNX_REPO_ID = "Scrybl/diar_streaming_sortformer_4spk-v2.1"
SORTFORMER_ONNX_REVISION = "3be92087de17e4e9063c9cfabc104f9809b2d037"
SORTFORMER_ONNX_FILENAME = "diar_streaming_sortformer_4spk-v2.1.onnx"
SORTFORMER_ONNX_SHA256 = "0182539aa7f8ecfc446ec56eb320b6f9131d686f98ca8f40644830af7d4d81c8"

_SAMPLE_RATE = 16_000
_N_FFT = 512
_WIN_LENGTH = 400
_HOP_LENGTH = 160
_N_MELS = 128
_PREEMPHASIS = 0.97
_LOG_ZERO_GUARD = 2.0**-24
_SUBSAMPLING = 8
_EMBEDDING_DIM = 512
_NUM_SPEAKERS = 4
_FRAME_DURATION = 0.08

_SILENCE_FRAMES_PER_SPEAKER = 3
_PRED_SCORE_THRESHOLD = 0.25
_STRONG_BOOST_RATE = 0.75
_WEAK_BOOST_RATE = 1.5
_MIN_POS_SCORES_RATE = 0.5
_SILENCE_THRESHOLD = 0.2


def _cached_sortformer_artifact(model_dir: str | Path | None = None) -> Path | None:
    if model_dir is not None:
        candidate = Path(model_dir)
        if candidate.is_dir():
            candidate /= SORTFORMER_ONNX_FILENAME
        return candidate if candidate.is_file() else None

    bundled = resolve_bundled_snapshot(SORTFORMER_ONNX_REPO_ID)
    if bundled is not None:
        candidate = bundled / SORTFORMER_ONNX_FILENAME
        if candidate.is_file():
            return candidate

    from huggingface_hub import try_to_load_from_cache  # noqa: PLC0415

    cached = try_to_load_from_cache(
        SORTFORMER_ONNX_REPO_ID,
        SORTFORMER_ONNX_FILENAME,
        revision=SORTFORMER_ONNX_REVISION,
        cache_dir=str(hf_cache_dir() / "hub"),
    )
    return Path(cached) if isinstance(cached, str) and Path(cached).is_file() else None


def _download_sortformer_artifact() -> Path:
    from huggingface_hub import hf_hub_download  # noqa: PLC0415

    return Path(
        hf_hub_download(
            SORTFORMER_ONNX_REPO_ID,
            SORTFORMER_ONNX_FILENAME,
            revision=SORTFORMER_ONNX_REVISION,
            cache_dir=str(hf_cache_dir() / "hub"),
        )
    )


class SortformerOnnxDiarizationManager:
    """Streaming Sortformer with a portable NumPy/librosa frontend."""

    backend = "sortformer"
    hf_token = None
    max_supported_speakers = _NUM_SPEAKERS

    _EXPECTED_INPUTS = {
        "chunk",
        "chunk_lengths",
        "spkcache",
        "spkcache_lengths",
        "fifo",
        "fifo_lengths",
    }
    _OUTPUTS = (
        "spkcache_fifo_chunk_preds",
        "chunk_pre_encode_embs",
        "chunk_pre_encode_lengths",
    )

    def __init__(
        self,
        device: str = "auto",
        *,
        provider: str = "auto",
        model_dir: str | Path | None = None,
        session: Any | None = None,
        session_factory: Callable[[Path, list[str]], Any] | None = None,
        artifact_resolver: Callable[[], Path | None] | None = None,
        artifact_downloader: Callable[[], Path] | None = None,
        checksum: str | None = SORTFORMER_ONNX_SHA256,
    ) -> None:
        if device not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError(f"Неподдерживаемое устройство Sortformer: {device}")
        if provider == "auto" and device == "cpu":
            provider = "cpu"
        elif provider == "auto" and device == "cuda":
            provider = "cuda"

        self.device = device
        self.provider = provider
        self.model_dir = model_dir
        self.session = session
        self._session_factory = session_factory or self._create_session
        self._artifact_resolver = artifact_resolver or (
            lambda: _cached_sortformer_artifact(self.model_dir)
        )
        self._artifact_downloader = artifact_downloader or _download_sortformer_artifact
        self._checksum = checksum
        self._model_path: Path | None = None
        self._inference_lock = threading.Lock()
        self._configure_streaming(session)
        self._reset_state()

    def _create_session(self, model_path: Path, providers: list[str]):
        import onnxruntime as ort  # noqa: PLC0415

        return ort.InferenceSession(str(model_path), providers=providers)

    @staticmethod
    def _metadata_int(session, name: str, default: int) -> int:
        if session is None:
            return default
        try:
            raw = session.get_modelmeta().custom_metadata_map.get(name)
            value = int(raw)
            return value if value > 0 else default
        except (AttributeError, TypeError, ValueError):
            return default

    def _configure_streaming(self, session) -> None:
        self.chunk_len = self._metadata_int(session, "chunk_len", 124)
        self.fifo_len = self._metadata_int(session, "fifo_len", 124)
        self.spkcache_len = self._metadata_int(session, "spkcache_len", 188)
        self.right_context = self._metadata_int(session, "right_context", 1)

    def _reset_state(self) -> None:
        self._spkcache = np.zeros((1, 0, _EMBEDDING_DIM), dtype=np.float32)
        self._spkcache_predictions: np.ndarray | None = None
        self._fifo = np.zeros((1, 0, _EMBEDDING_DIM), dtype=np.float32)
        self._fifo_predictions = np.zeros((1, 0, _NUM_SPEAKERS), dtype=np.float32)
        self._mean_silence_embedding = np.zeros((1, _EMBEDDING_DIM), dtype=np.float32)
        self._silence_frames = 0

    def _verify_checksum(self, model_path: Path) -> None:
        if not self._checksum:
            return
        digest = hashlib.sha256()
        with model_path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != self._checksum:
            raise RuntimeError(
                "Контрольная сумма ONNX-модели Sortformer не совпадает; "
                "удалите повреждённый файл и повторите загрузку"
            )

    def _ensure_session(self):
        if self.session is not None:
            return self.session
        model_path = self._artifact_resolver()
        if model_path is None:
            model_path = self._artifact_downloader()
        model_path = Path(model_path)
        if not model_path.is_file():
            raise FileNotFoundError(f"ONNX-модель Sortformer не найдена: {model_path}")
        self._verify_checksum(model_path)

        selection = resolve_onnx_providers(
            self.provider,
            available=available_onnx_providers(self.provider),
        )
        providers = onnx_session_providers(selection)
        session = self._session_factory(model_path, providers)
        input_names = {item.name for item in session.get_inputs()}
        output_names = {item.name for item in session.get_outputs()}
        if not self._EXPECTED_INPUTS.issubset(input_names):
            missing = sorted(self._EXPECTED_INPUTS - input_names)
            raise RuntimeError(f"ONNX Sortformer: отсутствуют входы {missing}")
        if not set(self._OUTPUTS).issubset(output_names):
            missing = sorted(set(self._OUTPUTS) - output_names)
            raise RuntimeError(f"ONNX Sortformer: отсутствуют выходы {missing}")
        self.session = session
        self._model_path = model_path
        self._configure_streaming(session)
        self._reset_state()
        return session

    def prepare(self, report=None, cancel_check=None):
        """Скачать отсутствующий ONNX и открыть ORT-сессию до первого файла."""
        emit = report or (lambda _state, **_kwargs: None)
        cancelled = cancel_check or (lambda: False)
        if cancelled():
            raise PreparationCancelled("Подготовка Sortformer ONNX отменена")

        if self.session is None:
            cached = self._artifact_resolver()
            if cached is None:
                emit(
                    PreparationState.DOWNLOADING,
                    message=f"Sortformer ONNX ({SORTFORMER_ONNX_REPO_ID})",
                )
                model_path = self._artifact_downloader()
                self._artifact_resolver = lambda: Path(model_path)
            session = self._ensure_session()
            provider_names = self._session_provider_names(session)
            emit(
                PreparationState.LOADING,
                message=(
                    "Sortformer ONNX Runtime: provider="
                    + ", ".join(provider_names)
                ),
                cached=cached is not None,
            )
        else:
            emit(
                PreparationState.LOADING,
                message=(
                    "Sortformer ONNX Runtime: provider="
                    + ", ".join(self._session_provider_names(self.session))
                ),
                cached=True,
            )

        if cancelled():
            raise PreparationCancelled("Подготовка Sortformer ONNX отменена")
        return self

    @staticmethod
    def _extract_features(audio: np.ndarray) -> np.ndarray:
        import librosa  # noqa: PLC0415

        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return np.zeros((1, 0, _N_MELS), dtype=np.float32)
        emphasized = samples.copy()
        emphasized[1:] -= _PREEMPHASIS * samples[:-1]
        spectrum = np.abs(
            librosa.stft(
                emphasized,
                n_fft=_N_FFT,
                hop_length=_HOP_LENGTH,
                win_length=_WIN_LENGTH,
                window="hann",
                center=True,
                pad_mode="constant",
            )
        ) ** 2
        mel_basis = librosa.filters.mel(
            sr=_SAMPLE_RATE,
            n_fft=_N_FFT,
            n_mels=_N_MELS,
            fmin=0.0,
            fmax=_SAMPLE_RATE / 2,
            htk=False,
            norm="slaney",
            dtype=np.float32,
        )
        # Accelerate/vecLib on macOS may leave floating-point status flags set
        # after an otherwise finite SGEMM; suppress those spurious warnings.
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            features = np.log(mel_basis @ spectrum + _LOG_ZERO_GUARD)
        return np.asarray(features.T[None, :, :], dtype=np.float32)

    def _process_features(self, features: np.ndarray) -> np.ndarray:
        total_frames = int(features.shape[1])
        stride = self.chunk_len * _SUBSAMPLING
        feed_size = (self.chunk_len + self.right_context) * _SUBSAMPLING
        chunks = math.ceil(total_frames / stride) if total_frames else 0
        predictions = []
        for index in range(chunks):
            start = index * stride
            end = min(start + feed_size, total_frames)
            current_len = end - start
            chunk = np.zeros((1, feed_size, _N_MELS), dtype=np.float32)
            chunk[:, :current_len, :] = features[:, start:end, :]
            predictions.append(self._streaming_update(chunk, current_len))
        if not predictions:
            return np.zeros((0, _NUM_SPEAKERS), dtype=np.float32)
        return np.concatenate(predictions, axis=0)

    def _streaming_update(self, chunk: np.ndarray, current_len: int) -> np.ndarray:
        session = self._ensure_session()
        cache_len = self._spkcache.shape[1]
        fifo_len = self._fifo.shape[1]
        inputs = {
            "chunk": np.asarray(chunk, dtype=np.float32),
            "chunk_lengths": np.asarray([current_len], dtype=np.int64),
            "spkcache": self._spkcache,
            "spkcache_lengths": np.asarray([cache_len], dtype=np.int64),
            "fifo": self._fifo,
            "fifo_lengths": np.asarray([fifo_len], dtype=np.int64),
        }
        raw_predictions, raw_embeddings, _lengths = session.run(list(self._OUTPUTS), inputs)
        raw_predictions = np.asarray(raw_predictions, dtype=np.float32)
        raw_embeddings = np.asarray(raw_embeddings, dtype=np.float32)
        valid_frames = math.ceil(current_len / _SUBSAMPLING)
        keep = min(self.chunk_len, valid_frames, raw_embeddings.shape[1])
        chunk_start = cache_len + fifo_len
        chunk_predictions = raw_predictions[0, chunk_start : chunk_start + keep, :]
        chunk_embeddings = raw_embeddings[:, :keep, :]
        previous_fifo_predictions = raw_predictions[0, cache_len : cache_len + fifo_len, :]

        self._fifo = np.concatenate((self._fifo, chunk_embeddings), axis=1)
        combined_predictions = np.concatenate(
            (previous_fifo_predictions, chunk_predictions),
            axis=0,
        )
        self._fifo_predictions = combined_predictions[None, :, :]

        if self._fifo.shape[1] > self.fifo_len:
            pop_count = max(
                self.chunk_len,
                max(0, valid_frames - self.fifo_len) + fifo_len,
            )
            pop_count = min(pop_count, self._fifo.shape[1])
            popped_embeddings = self._fifo[:, :pop_count, :].copy()
            popped_predictions = self._fifo_predictions[:, :pop_count, :].copy()
            self._update_silence_profile(popped_embeddings, popped_predictions)
            self._fifo = self._fifo[:, pop_count:, :].copy()
            self._fifo_predictions = self._fifo_predictions[:, pop_count:, :].copy()
            self._spkcache = np.concatenate((self._spkcache, popped_embeddings), axis=1)
            if self._spkcache_predictions is not None:
                self._spkcache_predictions = np.concatenate(
                    (self._spkcache_predictions, popped_predictions),
                    axis=1,
                )
            if self._spkcache.shape[1] > self.spkcache_len:
                if self._spkcache_predictions is None:
                    initial = raw_predictions[:, :cache_len, :].copy()
                    self._spkcache_predictions = np.concatenate(
                        (initial, popped_predictions),
                        axis=1,
                    )
                self._compress_speaker_cache()

        return chunk_predictions.copy()

    def _update_silence_profile(self, embeddings: np.ndarray, predictions: np.ndarray) -> None:
        silent = predictions[0].sum(axis=1) < _SILENCE_THRESHOLD
        for embedding in embeddings[0, silent, :]:
            total = self._mean_silence_embedding[0] * self._silence_frames + embedding
            self._silence_frames += 1
            self._mean_silence_embedding[0] = total / self._silence_frames

    def _compress_speaker_cache(self) -> None:
        if self._spkcache_predictions is None:
            return
        predictions = self._spkcache_predictions[0]
        frames = predictions.shape[0]
        per_speaker = self.spkcache_len // _NUM_SPEAKERS
        if per_speaker <= _SILENCE_FRAMES_PER_SPEAKER:
            self._spkcache = self._spkcache[:, : self.spkcache_len, :].copy()
            self._spkcache_predictions = self._spkcache_predictions[
                :, : self.spkcache_len, :
            ].copy()
            return

        usable = per_speaker - _SILENCE_FRAMES_PER_SPEAKER
        strong = int(usable * _STRONG_BOOST_RATE)
        weak = int(usable * _WEAK_BOOST_RATE)
        minimum_positive = int(usable * _MIN_POS_SCORES_RATE)
        probability = np.maximum(predictions, _PRED_SCORE_THRESHOLD)
        inverse = np.maximum(1.0 - predictions, _PRED_SCORE_THRESHOLD)
        scores = (
            np.log(probability)
            - np.log(inverse)
            + np.log(inverse).sum(axis=1, keepdims=True)
            - math.log(0.5)
        )
        positive_count = (scores > 0.0).sum(axis=0)
        disabled = (predictions <= 0.5) | (
            (scores <= 0.0) & (positive_count[None, :] >= minimum_positive)
        )
        scores[disabled] = -np.inf
        for speaker in range(_NUM_SPEAKERS):
            order = np.argsort(-scores[:, speaker], kind="stable")
            for count, scale in ((strong, 2.0), (weak, 1.0)):
                indices = order[: min(count, len(order))]
                valid = np.isfinite(scores[indices, speaker])
                scores[indices[valid], speaker] -= scale * math.log(0.5)

        padded = np.full(
            (frames + _SILENCE_FRAMES_PER_SPEAKER, _NUM_SPEAKERS),
            -np.inf,
            dtype=np.float32,
        )
        padded[:frames] = scores
        padded[frames:] = np.inf
        flat_scores = padded.T.reshape(-1)
        selected = np.argsort(-flat_scores, kind="stable")[: self.spkcache_len]
        selected = np.where(np.isneginf(flat_scores[selected]), 99_999, selected)
        selected.sort()
        frame_indices = selected % padded.shape[0]
        is_disabled = (selected == 99_999) | (frame_indices >= frames)
        frame_indices = np.where(is_disabled, 0, frame_indices)

        new_embeddings = np.zeros(
            (1, self.spkcache_len, _EMBEDDING_DIM),
            dtype=np.float32,
        )
        new_predictions = np.zeros(
            (1, self.spkcache_len, _NUM_SPEAKERS),
            dtype=np.float32,
        )
        for index, (frame, disabled_frame) in enumerate(
            zip(frame_indices, is_disabled, strict=True)
        ):
            if disabled_frame:
                new_embeddings[0, index] = self._mean_silence_embedding[0]
            else:
                new_embeddings[0, index] = self._spkcache[0, frame]
                new_predictions[0, index] = self._spkcache_predictions[0, frame]
        self._spkcache = new_embeddings
        self._spkcache_predictions = new_predictions

    @staticmethod
    def _speaker_segments(
        scores: np.ndarray,
        speaker: int,
        *,
        onset: float = 0.64,
        offset: float = 0.74,
        pad_onset: float = 0.06,
        pad_offset: float = 0.0,
        min_duration_on: float = 0.1,
        min_duration_off: float = 0.15,
    ) -> list[tuple[float, float, str]]:
        raw: list[tuple[float, float, str]] = []
        start: int | None = None
        for frame, probability in enumerate(scores):
            if start is None and probability >= onset:
                start = frame
            elif start is not None and probability < offset:
                begin = max(0.0, start * _FRAME_DURATION - pad_onset)
                end = frame * _FRAME_DURATION + pad_offset
                if end - begin >= min_duration_on:
                    raw.append((begin, end, f"speaker_{speaker}"))
                start = None
        if start is not None:
            begin = max(0.0, start * _FRAME_DURATION - pad_onset)
            end = len(scores) * _FRAME_DURATION + pad_offset
            if end - begin >= min_duration_on:
                raw.append((begin, end, f"speaker_{speaker}"))

        merged: list[tuple[float, float, str]] = []
        for segment in raw:
            if merged and segment[0] - merged[-1][1] < min_duration_off:
                merged[-1] = (merged[-1][0], segment[1], segment[2])
            else:
                merged.append(segment)
        return merged

    def _predictions_to_segments(self, predictions: np.ndarray, *, audio_duration: float):
        from ...utils.diarization import SpeakerSegment  # noqa: PLC0415

        raw = []
        for speaker in range(_NUM_SPEAKERS):
            raw.extend(self._speaker_segments(predictions[:, speaker], speaker))
        raw.sort(key=lambda item: (item[0], item[1], item[2]))
        return [
            SpeakerSegment(start, min(end, audio_duration), speaker)
            for start, end, speaker in raw
            if min(end, audio_duration) > start
        ]

    @staticmethod
    def _rename_speakers(segments):
        names: dict[str, str] = {}
        for segment in sorted(segments, key=lambda item: item.start):
            if segment.speaker not in names:
                names[segment.speaker] = f"Спикер №{len(names) + 1}"
            segment.speaker = names[segment.speaker]
        return segments

    def diarize(
        self,
        audio_path,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        progress_callback=None,
    ):
        del min_speakers, max_speakers
        if num_speakers is not None and not 1 <= num_speakers <= self.max_supported_speakers:
            raise ValueError("Sortformer поддерживает не более 4 спикеров")
        self._ensure_session()

        import librosa  # noqa: PLC0415
        import soundfile as sf  # noqa: PLC0415

        samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
        waveform = samples.mean(axis=1).astype(np.float32, copy=False)
        if sample_rate != _SAMPLE_RATE:
            waveform = librosa.resample(
                waveform,
                orig_sr=sample_rate,
                target_sr=_SAMPLE_RATE,
            ).astype(np.float32, copy=False)
        if progress_callback:
            progress_callback(0.05, None, None)
        features = self._extract_features(waveform)
        with self._inference_lock:
            self._reset_state()
            predictions = self._process_features(features)
        if progress_callback:
            progress_callback(0.9, None, None)
        segments = self._predictions_to_segments(
            predictions,
            audio_duration=len(waveform) / _SAMPLE_RATE,
        )
        segments = self._rename_speakers(segments)
        if progress_callback:
            progress_callback(1.0, None, None)
        return segments

    @staticmethod
    def _session_provider_names(session) -> list[str]:
        getter = getattr(session, "get_providers", None)
        if not callable(getter):
            return []
        return [str(provider) for provider in getter()]

    def smoke_test(self) -> dict[str, object]:
        """Выполнить один настоящий ORT-шаг без чтения аудиофайла."""
        self._ensure_session()
        feed_size = (self.chunk_len + self.right_context) * _SUBSAMPLING
        with self._inference_lock:
            self._reset_state()
            predictions = self._streaming_update(
                np.zeros((1, feed_size, _N_MELS), dtype=np.float32),
                feed_size,
            )
            self._reset_state()
        if predictions.shape != (self.chunk_len, _NUM_SPEAKERS):
            raise RuntimeError(
                f"ONNX Sortformer вернул неожиданную форму {predictions.shape}"
            )
        return {
            "frames": predictions.shape[0],
            "speakers": predictions.shape[1],
            "requested_provider": self.provider,
            "session_providers": self._session_provider_names(self.session),
        }

    def unload(self) -> None:
        self.session = None
        self._model_path = None
        self._reset_state()
