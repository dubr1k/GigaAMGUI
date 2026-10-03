"""MLX backend adapter for GigaAM RNNT inference."""

from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np

from ...config import ASR_SEGMENTATION_MODE, ASR_VAD_DEVICE
from .chunking import (
    AudioChunk,
    plan_audio_chunks,
    vad_regions_miss_active_audio,
)
from .longform import VadSegmenterCache, assemble_segments, call_logger, pyannote_vad_key
from .token_timestamps import tokens_to_words
from .types import BackendCapabilities, TranscriptionSegment, normalize_window_audio
from .vad import PyannoteVadSegmenter, VadSegmenter, VadUnavailableError, resolve_vad_device

# Conv1dSubsampling энкодера — два Conv1d со stride 2, то есть 4 кадра mel на кадр выхода.
_ENCODER_SUBSAMPLING = 4
# Тот же предел на число символов в кадре, что и в greedy-цикле gigaam_mlx.
_MAX_SYMBOLS_PER_FRAME = 10


class MLXBackend:
    """ASR backend implemented via ``gigaam_mlx``."""

    name = "mlx"
    # Как часто подрезать буферный пул MLX внутри цикла по окнам декодера.
    _CACHE_TRIM_INTERVAL = 32

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
        self._vad_cache = VadSegmenterCache(vad_segmenter_factory or PyannoteVadSegmenter)
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
                logger(f"Загружаем модель для Apple Silicon (MLX): {self.repo_id}")
                logger("При первом запуске модель скачивается — это может занять несколько минут.")

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
                    f"Не удалось загрузить модель MLX ({self.model_name}, {self.repo_id}): "
                    f"{type(exc).__name__}: {exc}"
                )
            return False

    def transcribe_longform(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> list[TranscriptionSegment]:
        if self.model is None:
            raise RuntimeError("MLX модель не загружена")

        with self._lock, call_logger(self, logger):
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
            finally:
                # Иначе буферы одного файла остаются в пуле MLX и суммируются
                # с буферами следующего файла пачки.
                self._empty_cache()

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

            segment: TranscriptionSegment = {
                "transcription": text,
                "boundaries": (start, end),
            }
            words = (item or {}).get("words")
            if words:
                segment["words"] = words
            segments.append(segment)

        return segments

    def transcribe_window(
        self,
        audio: np.ndarray,
        sample_rate: int,
        offset_samples: int,
    ) -> list[TranscriptionSegment]:
        if self.model is None or self._gigaam_mlx is None:
            raise RuntimeError("MLX backend is not initialized")
        if offset_samples < 0:
            raise ValueError("offset_samples must be non-negative")
        window = normalize_window_audio(audio, sample_rate)
        start = offset_samples / sample_rate
        with self._lock:
            text, words = self._decode_samples(
                window,
                decode_start_sec=start,
                duration=len(window) / 16_000,
            )
            text = text.strip()
        if not text:
            return []
        segment: TranscriptionSegment = {
            "transcription": text,
            "boundaries": (start, start + len(window) / 16_000),
        }
        if words is not None:
            segment["words"] = words
        return [segment]

    def _decode_samples(
        self,
        samples,
        *,
        decode_start_sec: float,
        duration: float,
    ) -> tuple[str, list[dict] | None]:
        """mel → энкодер → greedy RNNT; слова с абсолютными временами, если есть."""
        gm = self._gigaam_mlx
        mx = __import__("mlx.core", fromlist=["array"])  # lazy import
        mel = gm.audio.compute_mel(samples)
        encoded, seq_len = self.model.encode(mx.array(mel[None, :]))  # type: ignore[union-attr]
        mx.eval(encoded)
        decoded = self._decode_with_frames(encoded, seq_len, mx)
        if decoded is None:
            token_ids = self.model.decode(encoded, seq_len)  # type: ignore[union-attr]
            frames = None
        else:
            token_ids, frames = decoded
        text = str(self.tokenizer.decode(token_ids) if self.tokenizer is not None else "")
        words = self._words_from_frames(
            token_ids,
            frames,
            decode_start_sec=decode_start_sec,
            duration=duration,
        )
        return text, words

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
            self._logger(f"Внимание: {reason}")
        return self._legacy_chunks(audio)

    def _use_overlap_chunks(self, audio, reason: str) -> list[AudioChunk]:
        gm = self._gigaam_mlx
        if gm is None:
            raise RuntimeError("MLX backend is not initialized")
        self.segmentation_mode = "overlap_chunks"
        self.segmentation_fallback_reason = reason
        if self._logger is not None:
            self._logger(f"Внимание: {reason}")
        total_seconds = float(len(audio)) / gm.audio.SAMPLE_RATE if len(audio) else 0.0
        return plan_audio_chunks(
            audio,
            [(0.0, total_seconds)],
            sample_rate=gm.audio.SAMPLE_RATE,
            max_chunk_seconds=20.0,
        )

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

        vad_device = resolve_vad_device(ASR_VAD_DEVICE)
        hf_token, segmenter_key = pyannote_vad_key(vad_device)
        try:
            boundaries = self._vad_cache.segment(
                segmenter_key,
                audio_path,
                audio_duration=total_seconds,
                token=hf_token,
                device=vad_device,
            )
        except Exception as exc:
            return self._use_overlap_chunks(
                audio,
                self._vad_fallback_reason(exc),
            )

        gm = self._gigaam_mlx
        if gm is None:
            raise RuntimeError("MLX backend is not initialized")
        if vad_regions_miss_active_audio(audio, boundaries, sample_rate=gm.audio.SAMPLE_RATE):
            return self._use_overlap_chunks(
                audio,
                "VAD пропустил длинный участок с активным звуком; "
                "использовано полное разбиение MLX по тихим точкам с перекрытием",
            )
        self.segmentation_mode = "vad"
        self.segmentation_fallback_reason = None
        chunks = self._chunks_from_vad_boundaries(audio, boundaries)
        if self._logger is not None:
            self._logger(
                f"Речь найдена: участков — {len(boundaries)}, "
                f"фрагментов для распознавания — {len(chunks)}"
            )
        return chunks

    def _frame_seconds(self) -> float:
        """Длительность одного кадра энкодера в секундах."""
        audio = getattr(self._gigaam_mlx, "audio", None)
        hop = float(getattr(audio, "HOP_LENGTH", 160))
        sample_rate = float(getattr(audio, "SAMPLE_RATE", 16000))
        if hop <= 0.0 or sample_rate <= 0.0:
            return 0.0
        return hop / sample_rate * _ENCODER_SUBSAMPLING

    def _decode_with_frames(self, encoded, seq_len, mx) -> tuple[list[int], list[int]] | None:
        """Greedy RNNT-декодирование, запоминающее кадр эмиссии каждого токена.

        RNNT кадрово-синхронный, и индекс кадра уже есть в самом цикле, но
        ``gigaam_mlx.decode`` возвращает только токены — поэтому цикл повторён
        здесь. Модели без RNNT-головы обслуживает штатный ``model.decode``.
        """
        model = self.model
        decoder = getattr(model, "decoder", None)
        joint = getattr(model, "joint", None)
        blank_id = getattr(decoder, "blank_id", None)
        if getattr(model, "model_type", None) != "rnnt" or joint is None or blank_id is None:
            return None

        enc = encoded[0]
        tokens: list[int] = []
        frames: list[int] = []
        state = None
        last_label = None
        for frame in range(int(seq_len)):
            step = enc[:, frame : frame + 1].T
            step = mx.expand_dims(step, axis=0) if step.ndim == 2 else step
            for _symbol in range(_MAX_SYMBOLS_PER_FRAME):
                prediction, new_state = decoder.predict(last_label, state)
                token = int(mx.argmax(joint(step, prediction)[0, 0, 0, :]).item())
                if token == blank_id:
                    break
                tokens.append(token)
                frames.append(frame)
                state = new_state
                last_label = mx.array([[token]])
        return tokens, frames

    def _words_from_frames(
        self,
        token_ids,
        frames: list[int] | None,
        *,
        decode_start_sec: float,
        duration: float,
    ) -> list[dict] | None:
        id_to_piece = getattr(self.tokenizer, "id_to_piece", None)
        if frames is None or id_to_piece is None:
            return None

        frame_seconds = self._frame_seconds()
        if frame_seconds <= 0.0:
            return None
        try:
            pieces = [str(id_to_piece(int(token_id))) for token_id in token_ids]
        except Exception:
            return None

        relative_words = tokens_to_words(
            pieces,
            [frame * frame_seconds for frame in frames],
            duration=duration,
        )
        if relative_words is None:
            return None
        return [
            {
                "text": word["text"],
                "start": round(decode_start_sec + word["start"], 9),
                "end": round(decode_start_sec + word["end"], 9),
            }
            for word in relative_words
        ]

    @staticmethod
    def _read_prepared_wav(audio_path: str, sample_rate: int):
        """Прочитать уже готовый 16 кГц моно WAV, не поднимая ffmpeg повторно.

        ``gigaam_mlx.load_audio`` всегда запускает ffmpeg, хотя ``processor``
        заранее конвертирует вход в 16 кГц моно WAV. Повторный декод — это ещё
        один полный проход по файлу и одновременно живущие ``stdout``-байты и
        float32-массив. Возвращает ``None``, если формат не совпал: тогда
        вызывающий код падает обратно на ffmpeg.
        """
        try:
            import soundfile as sf

            info = sf.info(audio_path)
            if info.samplerate != sample_rate or info.channels != 1:
                return None
            audio, _ = sf.read(audio_path, dtype="float32")
            return np.asarray(audio, dtype=np.float32).reshape(-1)
        except Exception:
            return None

    def _transcribe_in_chunks(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
    ) -> list[dict]:
        gm = self._gigaam_mlx
        if gm is None:
            raise RuntimeError("MLX backend is not initialized")

        sample_rate = gm.audio.SAMPLE_RATE
        audio = self._read_prepared_wav(audio_path, sample_rate)
        if audio is None:
            audio = gm.load_audio(audio_path)
        total_samples = len(audio)
        total_seconds = float(total_samples) / sample_rate if total_samples else 0.0
        chunks = self._resolve_chunks(
            audio_path,
            audio,
            total_seconds=total_seconds,
        )

        def decode(chunk: AudioChunk) -> tuple[str, list[dict] | None]:
            start_sample = chunk.decode_start_sample
            end_sample = chunk.decode_end_sample
            return self._decode_samples(
                audio[start_sample:end_sample],
                decode_start_sec=float(start_sample) / sample_rate,
                duration=float(end_sample - start_sample) / sample_rate,
            )

        def trim_cache(chunk_index: int) -> None:
            # Окна разной длины дают буферы разного размера, и MLX кэширует
            # каждый размер отдельно. На часовой записи это сотни окон, поэтому
            # пул нужно подрезать по ходу, а не только в конце файла.
            if (chunk_index + 1) % self._CACHE_TRIM_INTERVAL == 0:
                self._empty_cache()

        segments = assemble_segments(
            chunks,
            decode,
            total_seconds=total_seconds,
            progress_callback=progress_callback,
            on_chunk_done=trim_cache,
        )
        # Исторический формат MLX-цикла: start/end/text (+words).
        result_segments: list[dict] = []
        for segment in segments:
            start_time, end_time = segment["boundaries"]
            item: dict = {"start": start_time, "end": end_time, "text": segment["transcription"]}
            if "words" in segment:
                item["words"] = segment["words"]
            result_segments.append(item)
        return result_segments

    def _empty_cache(self) -> None:
        """Вернуть системе буферный пул MLX.

        ``clear_cache`` живёт в ``mlx.core``: у пакета верхнего уровня ``mlx``
        такого атрибута нет. Прежний ``getattr(mlx, "clear_cache", None)``
        всегда возвращал ``None``, промах глушился ``except`` — и пул рос до
        десятков гигабайт при пиковой потребности ~1.5 ГБ. На Apple Silicon это
        unified memory, поэтому съедалась именно системная память, а не только
        видеопамять, и в RSS процесса рост не был виден.
        """
        try:
            mx = __import__("mlx.core", fromlist=["clear_cache"])
            mx.clear_cache()
        except Exception:
            pass

    def unload(self) -> None:
        with self._lock:
            self.model = None
            self.tokenizer = None
            self._vad_cache.reset()
            self.segmentation_mode = "not_run"
            self.segmentation_fallback_reason = None
            self._empty_cache()

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
