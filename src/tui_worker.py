"""JSON Lines worker for the optional GigaAM terminal UI.

The worker deliberately owns model execution while the TUI owns terminal state.  It
writes protocol messages only to stdout; callers should treat stderr as
human-readable diagnostics.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Те же фильтры, что и в app.py: deprecation-шум pyannote/speechbrain/torchaudio
# уходит в stderr, который клиенты показывают пользователю как диагностику.
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")
warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")
warnings.filterwarnings("ignore", message=".*torchaudio.*deprecated.*")
warnings.filterwarnings("ignore", message=".*speechbrain.pretrained.*deprecated.*")

from src.core.subtitles import SubtitleOptions  # noqa: E402
from src.services.live_worker_service import LiveWorkerService  # noqa: E402
from src.services.llm_worker_service import LLMWorkerService  # noqa: E402
from src.utils.output_naming import find_output_collisions  # noqa: E402


class TuiWorker:
    """Runs one transcription batch at a time and exposes it over JSONL."""

    def __init__(self, output=None):
        self._output = output or sys.stdout
        self._write_lock = threading.Lock()
        self._task: threading.Thread | None = None
        self._cancel_requested = threading.Event()
        self._llm = LLMWorkerService(self.emit)
        self._live = LiveWorkerService(self.emit)

    def emit(self, message_type: str, **payload: Any) -> None:
        message = {"type": message_type, **payload}
        with self._write_lock:
            self._output.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._output.flush()

    def _log(self, message: str) -> None:
        self.emit("log", message=str(message))

    def handle(self, command: dict[str, Any]) -> None:
        command_type = command.get("type")
        if command_type == "ping":
            self.emit("pong")
        elif command_type == "start":
            self._start(command)
        elif command_type == "cancel":
            self._cancel()
        elif command_type == "llm_start":
            if self._busy():
                self.emit("error", message="Processing is already running")
            else:
                self._llm.start(command)
        elif command_type == "llm_cancel":
            self._llm.cancel()
        elif command_type == "llm_tools":
            self._llm_tools(command)
        elif command_type == "llm_tool_check":
            self._llm_tool_check(command)
        elif command_type == "live_start":
            if self._busy():
                self.emit("error", message="Processing is already running")
            else:
                self._live.start(command)
        elif command_type in self._LIVE_COMMANDS:
            self._LIVE_COMMANDS[command_type](self, command)
        else:
            self.emit("error", message=f"Unknown command: {command_type!r}")

    _LIVE_COMMANDS = {
        "live_audio": lambda self, command: self._live.audio(command),
        "live_capture_event": lambda self, command: self._live.capture_event(command),
        "live_pause": lambda self, command: self._live.pause(),
        "live_resume": lambda self, command: self._live.resume(),
        "live_stop": lambda self, command: self._live.stop(),
        "live_ask": lambda self, command: self._live.ask(command),
        "live_ask_cancel": lambda self, command: self._live.ask_cancel(),
    }

    def _llm_tools(self, command: dict[str, Any]) -> None:
        """Реестр провайдеров + статусы CLI для нативных фронтендов (Liquid, TUI)."""
        from src.services import cli_tools

        overrides = command.get("overrides") or {}
        statuses = cli_tools.scan(overrides, fresh=bool(command.get("fresh")))
        self.emit(
            "llm_tools",
            providers=cli_tools.canonical_provider_names(),
            tools=[status.to_dict() for status in statuses],
        )

    def _llm_tool_check(self, command: dict[str, Any]) -> None:
        from src.services import cli_tools

        try:
            spec = cli_tools.provider_by_name(str(command.get("provider") or ""))
        except KeyError:
            self.emit("error", message=f"Unknown LLM provider: {command.get('provider')!r}")
            return
        status = cli_tools.resolve_tool(spec, command.get("path") or None)
        self.emit("llm_tool_check", tool=status.to_dict())

    def _busy(self) -> bool:
        """Batch, LLM and live work share one worker and exclude each other."""
        return bool(self._task and self._task.is_alive()) or self._llm.is_running() or self._live.is_running()

    def _start(self, command: dict[str, Any]) -> None:
        if self._busy():
            self.emit("error", message="Processing is already running")
            return
        files = [str(path) for path in command.get("files", []) if str(path).strip()]
        if not files:
            self.emit("error", message="No input files supplied")
            return
        missing = [path for path in files if not Path(path).is_file()]
        if missing:
            self.emit("error", message=f"Input file does not exist: {missing[0]}")
            return
        output_dir = str(command.get("output_dir") or "")
        collisions = find_output_collisions(files, output_dir or None)
        if collisions:
            names = ", ".join(sorted(Path(path).name for group in collisions for path in group))
            self.emit("error", message=f"Input files would overwrite the same output names: {names}")
            return
        formats = command.get("formats") or ["txt"]
        if not isinstance(formats, list) or not all(isinstance(item, str) for item in formats):
            self.emit("error", message="formats must be an array of strings")
            return
        sentence_split = command.get("subtitle_sentence_split", True)
        if not isinstance(sentence_split, bool):
            self.emit("error", message="subtitle_sentence_split must be a boolean")
            return
        try:
            subtitle_options = SubtitleOptions(
                sentence_split=sentence_split,
                max_line_count=command.get("subtitle_max_lines", 2),
                max_line_width=command.get("subtitle_max_width", 64),
            )
        except (TypeError, ValueError) as exc:
            self.emit("error", message=str(exc))
            return
        audio_preprocessing_mode = str(command.get("audio_preprocessing_mode") or "auto")
        if audio_preprocessing_mode not in {"auto", "off", "light", "denoise"}:
            self.emit("error", message=f"Unknown audio preprocessing mode: {audio_preprocessing_mode!r}")
            return
        self._cancel_requested.clear()
        self._task = threading.Thread(
            target=self._run_batch,
            args=(
                files,
                output_dir,
                formats,
                bool(command.get("diarization", False)),
                command.get("diarization_backend") or "pyannote",
                command.get("num_speakers"),
                command.get("backend") or "auto",
                command.get("model") or "v3_e2e_rnnt",
                command.get("onnx_provider") or "auto",
                audio_preprocessing_mode,
                subtitle_options.sentence_split,
                subtitle_options.max_line_count,
                subtitle_options.max_line_width,
            ),
            daemon=True,
        )
        self._task.start()

    def _cancel(self) -> None:
        if not self._task or not self._task.is_alive():
            self.emit("error", message="Nothing is being processed")
            return
        self._cancel_requested.set()
        # The shared processor has no safe mid-file cancellation mechanism.  This
        # matches the GUI: finish the current file, then stop the remaining queue.
        self.emit("cancelling", message="Остановка запрошена: закончим текущий файл и остановимся.")

    def _run_batch(
        self,
        files,
        output_dir,
        formats,
        diarization,
        diarization_backend,
        num_speakers,
        backend,
        model,
        onnx_provider,
        audio_preprocessing_mode,
        subtitle_sentence_split,
        subtitle_max_lines,
        subtitle_max_width,
    ) -> None:
        started_at = time.monotonic()
        results: list[dict[str, Any]] = []
        try:
            # Keep the protocol health check lightweight: ML dependencies load only
            # when a batch actually starts.
            from src.config import STATS_FILE
            from src.core.model_loader import ModelLoader
            from src.core.progress import ProgressEvent
            from src.services.transcription_service import build_processor
            from src.utils.processing_stats import ProcessingStats

            self.emit("started", files=files, total_files=len(files), backend=backend)
            loader = ModelLoader(
                requested_backend=backend,
                model_revision=model,
                onnx_provider=onnx_provider,
            )
            self._log("Загружаем модель распознавания речи…")
            if not loader.load_model(logger=self._log):
                self.emit("completed", success=False, cancelled=False, results=[], message="Не удалось загрузить модель распознавания")
                return
            self._log("Модель загружена.")
            stats = ProcessingStats(STATS_FILE)
            current: dict[str, Any] = {"index": 0, "file": files[0]}

            def progress(event_or_stage, value=None):
                if isinstance(event_or_stage, ProgressEvent):
                    event = event_or_stage
                    payload = {
                        "stage": event.stage,
                        "stage_progress": event.stage_progress,
                        "file_progress": event.file_progress,
                        "processed_seconds": event.processed_seconds,
                        "total_seconds": event.total_seconds,
                        "message": event.message,
                    }
                elif isinstance(event_or_stage, dict):
                    payload = dict(event_or_stage)
                else:
                    payload = {"stage": str(event_or_stage), "file_progress": float(value or 0.0)}
                self.emit("progress", file=current["file"], file_index=current["index"], total_files=len(files), **payload)

            processor = build_processor(loader, stats, logger=self._log, progress_callback=progress)
            for index, filepath in enumerate(files):
                if self._cancel_requested.is_set():
                    break
                current.update(index=index, file=filepath)
                self.emit("file_started", file=filepath, file_index=index, total_files=len(files))
                file_output_dir = output_dir or os.path.dirname(filepath)
                try:
                    result = processor.process_file(
                        filepath=filepath,
                        output_dir=file_output_dir,
                        file_index=index,
                        total_files=len(files),
                        enable_diarization=diarization,
                        diarization_backend=diarization_backend,
                        audio_preprocessing_mode=audio_preprocessing_mode,
                        num_speakers=num_speakers if isinstance(num_speakers, int) and num_speakers > 0 else None,
                        output_formats=formats,
                        subtitle_options=SubtitleOptions(
                            sentence_split=subtitle_sentence_split,
                            max_line_count=subtitle_max_lines,
                            max_line_width=subtitle_max_width,
                        ),
                    )
                except Exception as exc:
                    self._log(f"Не удалось обработать {os.path.basename(filepath)}: {exc}")
                    result = {"file_path": filepath, "success": False, "error": str(exc), "saved_files": []}
                results.append(result)
                if result.get("success") and result.get("media_duration", 0) > 0:
                    stats.add_processing_record(
                        file_path=result.get("file_path", filepath), file_size=result.get("file_size", 0),
                        duration=result.get("media_duration", 0), conversion_time=result.get("conversion_time", 0),
                        transcription_time=result.get("transcription_time", 0), success=True,
                    )
                self.emit("file_completed", file=filepath, file_index=index, result=result)
            cancelled = self._cancel_requested.is_set()
            success = bool(results) and all(result.get("success") for result in results) and not cancelled
            self.emit("completed", success=success, cancelled=cancelled, results=results, elapsed_seconds=time.monotonic() - started_at)
        except ModuleNotFoundError as exc:
            dependency = exc.name or "a required package"
            self.emit(
                "error",
                message=(
                    f"Python environment is missing {dependency}. "
                    "Install project dependencies with `python -m pip install -r requirements.txt` "
                    "or launch TUI with GIGAAM_PYTHON pointing to the configured environment."
                ),
                traceback=traceback.format_exc(),
            )
            self.emit("completed", success=False, cancelled=False, results=results, elapsed_seconds=time.monotonic() - started_at)
        except Exception as exc:  # Keep JSONL valid even for startup failures.
            self.emit("error", message=str(exc), traceback=traceback.format_exc())
            self.emit("completed", success=False, cancelled=False, results=results, elapsed_seconds=time.monotonic() - started_at)


def read_commands(stream) -> Iterator[dict[str, Any]]:
    """JSONL-команды из ``stream`` (двоичный, небуферизованный), устойчиво к
    неблокирующему stdin.

    Дочерние CLI (замечено у ``pi --version``) выставляют O_NONBLOCK на
    унаследованный stdin; флаг живёт на общем описании открытого файла, так что
    неблокирующим становится и наш конец трубы. ``for line in sys.stdin`` тогда
    получает EAGAIN, Python отдаёт «EOF», и воркер молча завершается, пока TUI ещё
    держит трубу («Worker exited»). Здесь EAGAIN — не конец: ждём данных через
    select и читаем дальше. Невалидные строки отдаются как ``{"_invalid": …}``.
    """
    import errno
    import select

    fd = stream.fileno()
    buffer = b""
    while True:
        try:
            chunk = stream.read(65536)
        except BlockingIOError:
            select.select([fd], [], [], 1.0)
            continue
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                select.select([fd], [], [], 1.0)
                continue
            raise
        if chunk is None:  # BufferedReader на неблокирующем fd
            select.select([fd], [], [], 1.0)
            continue
        if chunk == b"":
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError("Command must be a JSON object")
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
                yield {"_invalid": f"Invalid command: {exc}"}
                continue
            yield command


def main() -> int:
    worker = TuiWorker()
    stdin = getattr(sys.stdin, "buffer", sys.stdin)
    for command in read_commands(stdin):
        if "_invalid" in command:
            worker.emit("error", message=command["_invalid"])
            continue
        worker.handle(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
