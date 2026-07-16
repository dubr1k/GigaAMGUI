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
from pathlib import Path
from typing import Any


class TuiWorker:
    """Runs one transcription batch at a time and exposes it over JSONL."""

    def __init__(self, output=None):
        self._output = output or sys.stdout
        self._write_lock = threading.Lock()
        self._task: threading.Thread | None = None
        self._cancel_requested = threading.Event()

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
            self._start_llm(command)
        else:
            self.emit("error", message=f"Unknown command: {command_type!r}")

    def _start(self, command: dict[str, Any]) -> None:
        if self._task and self._task.is_alive():
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
        formats = command.get("formats") or ["txt"]
        if not isinstance(formats, list) or not all(isinstance(item, str) for item in formats):
            self.emit("error", message="formats must be an array of strings")
            return
        self._cancel_requested.clear()
        self._task = threading.Thread(
            target=self._run_batch,
            args=(files, str(command.get("output_dir") or ""), formats, bool(command.get("diarization", False)), command.get("diarization_backend") or "pyannote", command.get("num_speakers"), command.get("backend") or "auto", command.get("model") or "v3_e2e_rnnt"),
            daemon=True,
        )
        self._task.start()

    def _start_llm(self, command: dict[str, Any]) -> None:
        if self._task and self._task.is_alive():
            self.emit("error", message="Processing is already running")
            return
        paths = [Path(str(value)) for value in command.get("files", [command.get("file")]) if str(value or "")]
        if not paths or any(not path.is_file() for path in paths):
            self.emit("error", message="LLM transcript file does not exist")
            return
        modes = command.get("modes", [command.get("mode") or "summary"])
        if not isinstance(modes, list) or not modes or not all(isinstance(mode, str) for mode in modes):
            self.emit("error", message="Select at least one LLM mode")
            return
        prompts = {
            "summary": "Сделай плотную выжимку: ключевые факты, решения, риски и открытые вопросы.",
            "tasks": "Выдели конкретные задачи, ответственных, сроки и открытые вопросы.",
            "terms": "Выдели основные термины, имена, организации, сокращения и их контекст.",
        }
        custom_prompt = str(command.get("prompt") or "").strip()
        if "custom" in modes and not custom_prompt:
            self.emit("error", message="Custom LLM prompt is required")
            return
        settings = command.get("settings")
        if not isinstance(settings, dict):
            self.emit("error", message="LLM settings are required")
            return
        self._task = threading.Thread(target=self._run_llm, args=(paths, modes, custom_prompt, prompts, settings, str(command.get("output_dir") or "")), daemon=True)
        self._task.start()

    def _run_llm(self, paths: list[Path], modes: list[str], custom_prompt: str, prompts: dict[str, str], settings: dict[str, Any], output_dir: str) -> None:
        try:
            from src.services import llm_service

            # One request per selected mode, with every text result accumulated since
            # the last /clear as its context.
            transcript = "\n\n".join(
                f"--- {path.name} ---\n{path.read_text(encoding='utf-8')}" for path in paths
            )
            target = Path(output_dir) if output_dir else paths[-1].parent
            target.mkdir(parents=True, exist_ok=True)
            saved_files = []
            for index, mode in enumerate(dict.fromkeys(modes), 1):
                prompt = custom_prompt if mode == "custom" else prompts.get(mode, custom_prompt)
                if not prompt:
                    raise ValueError(f"Unknown LLM mode: {mode}")
                self.emit("llm_started", mode=mode, index=index, total=len(modes))
                answer = llm_service.run_provider(settings, transcript, prompt, provider=str(settings.get("provider") or "API"), strict_empty_cli=True)
                saved = target / f"session_llm_{mode}.txt"
                saved.write_text(answer, encoding="utf-8")
                saved_files.append(str(saved))
            self.emit("llm_completed", success=True, saved_files=saved_files)
        except Exception as exc:
            self.emit("llm_completed", success=False, message=str(exc))

    def _cancel(self) -> None:
        if not self._task or not self._task.is_alive():
            self.emit("error", message="Nothing is being processed")
            return
        self._cancel_requested.set()
        # The shared processor has no safe mid-file cancellation mechanism.  This
        # matches the GUI: finish the current file, then stop the remaining queue.
        self.emit("cancelling", message="Cancellation requested; stopping after the current file")

    def _run_batch(self, files, output_dir, formats, diarization, diarization_backend, num_speakers, backend, model) -> None:
        started_at = time.monotonic()
        results: list[dict[str, Any]] = []
        try:
            # Keep the protocol health check lightweight: ML dependencies load only
            # when a batch actually starts.
            from src.config import AUDIO_PREPROCESSING_MODE, STATS_FILE
            from src.core.model_loader import ModelLoader
            from src.core.progress import ProgressEvent
            from src.services.transcription_service import build_processor
            from src.utils.processing_stats import ProcessingStats

            self.emit("started", files=files, total_files=len(files), backend=backend)
            loader = ModelLoader(requested_backend=backend, model_revision=model)
            self._log("Loading GigaAM model…")
            if not loader.load_model(logger=self._log):
                self.emit("completed", success=False, cancelled=False, results=[], message="Failed to load model")
                return
            self._log("GigaAM model ready")
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
                        audio_preprocessing_mode=AUDIO_PREPROCESSING_MODE,
                        num_speakers=num_speakers if isinstance(num_speakers, int) and num_speakers > 0 else None,
                        output_formats=formats,
                    )
                except Exception as exc:
                    self._log(f"Error while processing {os.path.basename(filepath)}: {exc}")
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


def main() -> int:
    worker = TuiWorker()
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if not isinstance(command, dict):
                raise ValueError("Command must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            worker.emit("error", message=f"Invalid command: {exc}")
            continue
        worker.handle(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
