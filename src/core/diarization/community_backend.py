"""Community-1 in a separate Python environment, preserving the 3.1 runtime."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from collections import deque
from pathlib import Path

from ...utils.model_cache import hf_hub_cache_dir, hf_repo_is_cached, resolve_model_dir
from ..model_preparation import PreparationCancelled, PreparationState
from .base import SpeakerSegment
from .community_runtime import ensure_runtime, install_hint, isolated_environment
from .mapping import SpeakerMappingMixin

_COMMUNITY_MODEL_ID = "pyannote/speaker-diarization-community-1"
_WORKER = Path(__file__).with_name("community_worker.py")


class CommunityDiarizationManager(SpeakerMappingMixin):
    """Reuse a private pyannote 4 process for all files handled by this manager."""

    backend = "pyannote-community"

    def __init__(
        self,
        hf_token: str | None = None,
        device: str = "auto",
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        model_dir: str | Path | None = None,
    ):
        self.hf_token = hf_token or os.environ.get("HF_TOKEN")
        self.device = device
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        explicit = model_dir or os.environ.get("GIGAAM_COMMUNITY_MODEL_DIR")
        self.model_dir = resolve_model_dir(_COMMUNITY_MODEL_ID, explicit=explicit)
        self._process: subprocess.Popen | None = None
        self._stderr: deque[str] = deque(maxlen=80)
        self._stderr_thread: threading.Thread | None = None
        self._stdout: queue.Queue[str | None] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def _redact(self, text: str) -> str:
        return text.replace(self.hf_token, "[REDACTED]") if self.hf_token else text

    @staticmethod
    def _drain_stderr(process: subprocess.Popen, lines: deque[str], token: str | None) -> None:
        if process.stderr is not None:
            for line in process.stderr:
                text = line.rstrip()
                lines.append(text.replace(token, "[REDACTED]") if token else text)

    @staticmethod
    def _drain_stdout(process: subprocess.Popen, lines: queue.Queue[str | None]) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    lines.put(line)
        finally:
            lines.put(None)

    def _start(self, report=None, cancel_check=None) -> None:
        if self._process is not None:
            return
        emit = report or (lambda _state, **_kwargs: None)
        cancelled = cancel_check or (lambda: False)
        python = ensure_runtime(emit, cancelled)
        if self.model_dir is None and not hf_repo_is_cached(_COMMUNITY_MODEL_ID):
            emit(PreparationState.DOWNLOADING, message=_COMMUNITY_MODEL_ID)
        emit(PreparationState.LOADING, message=_COMMUNITY_MODEL_ID)
        if not _WORKER.is_file():
            raise RuntimeError("Community-1 worker отсутствует в приложении; переустановите сборку.")

        env = isolated_environment()
        self._stderr.clear()
        try:
            process = subprocess.Popen(
                [str(python), "-I", str(_WORKER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise RuntimeError(f"Не удалось запустить Community-1 runtime: {exc}\n{install_hint()}") from exc
        self._process = process
        self._stdout = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout, args=(process, self._stdout), daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(process, self._stderr, self.hf_token), daemon=True
        )
        self._stderr_thread.start()
        try:
            self._request({
                "action": "prepare",
                "token": self.hf_token,
                "model_dir": str(self.model_dir) if self.model_dir is not None else None,
                "cache_dir": str(hf_hub_cache_dir()),
                "device": self.device,
            }, cancel_check=cancelled)
        except BaseException:
            self._stop()
            raise

    def _request(self, request: dict, progress_callback=None, cancel_check=None):
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("Community-1 worker не запущен")
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False, allow_nan=False) + "\n")
            process.stdin.flush()
            while True:
                if cancel_check is not None and cancel_check():
                    raise PreparationCancelled("Подготовка Community-1 отменена")
                try:
                    line = self._stdout.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    code = process.wait()
                    if self._stderr_thread is not None:
                        self._stderr_thread.join(timeout=1)
                    details = self._redact("\n".join(self._stderr))[-6000:]
                    raise RuntimeError(f"Community-1 worker завершился с кодом {code}. {details}")
                try:
                    message = json.loads(line)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("Некорректный ответ Community-1 worker") from exc
                if not isinstance(message, dict):
                    raise RuntimeError("Некорректный ответ Community-1 worker")
                if message.get("event") == "progress":
                    if progress_callback is not None:
                        progress_callback(None, None, None)
                    continue
                if message.get("event") == "error":
                    detail = self._redact(str(message.get("message", "Ошибка Community-1")))
                    if message.get("kind") == "runtime":
                        detail += "\n" + install_hint()
                    raise RuntimeError(detail)
                if message.get("event") == "result":
                    return message.get("value")
                raise RuntimeError("Неизвестный ответ Community-1 worker")
        except BaseException:
            self._stop()
            raise

    def prepare(self, report=None, cancel_check=None):
        emit = report or (lambda _state, **_kwargs: None)
        cancelled = cancel_check or (lambda: False)
        if cancelled():
            raise PreparationCancelled("Подготовка Community-1 отменена")
        with self._lock:
            self._start(report=emit, cancel_check=cancelled)
        if cancelled():
            self.unload()
            raise PreparationCancelled("Подготовка Community-1 отменена")
        return self

    def diarize(
        self,
        audio_path: Path | str,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        progress_callback=None,
    ) -> list[SpeakerSegment]:
        options = {}
        if num_speakers is not None:
            options["num_speakers"] = num_speakers
        else:
            minimum = min_speakers if min_speakers is not None else self.min_speakers
            maximum = max_speakers if max_speakers is not None else self.max_speakers
            if minimum is not None:
                options["min_speakers"] = minimum
            if maximum is not None:
                options["max_speakers"] = maximum
        try:
            with self._lock:
                self._start()
                result = self._request({
                    "action": "diarize",
                    "audio_path": str(audio_path),
                    "options": options,
                }, progress_callback=progress_callback)
            segments = [SpeakerSegment(float(turn[0]), float(turn[1]), str(turn[2])) for turn in result]
            segments.sort(key=lambda segment: segment.start)
            return self._rename_speakers(segments)
        except Exception as exc:
            raise ValueError(f"Ошибка при диаризации Community-1: {self._redact(str(exc))}") from exc

    def _stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1)
            self._stderr_thread = None
        if self._stdout_thread is not None:
            self._stdout_thread.join(timeout=1)
            self._stdout_thread = None
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def unload(self) -> None:
        with self._lock:
            self._stop()

    def __del__(self):
        try:
            self.unload()
        except Exception:
            pass
