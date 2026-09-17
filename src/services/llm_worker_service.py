"""JSONL-facing LLM runner shared by the TUI worker and the native macOS client."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.services import llm_service

PROMPTS = {
    "summary": "Сделай плотную выжимку: ключевые факты, решения, риски и открытые вопросы.",
    "tasks": "Выдели конкретные задачи, ответственных, сроки и открытые вопросы.",
    "terms": "Выдели основные термины, имена, организации, сокращения и их контекст.",
}


class _Cancelled(Exception):
    pass


class LLMWorkerService:
    """Run one `llm_start` request at a time and report progress as JSONL events."""

    def __init__(
        self,
        emit: Callable[..., None],
        *,
        run_provider: Callable[..., str] = llm_service.run_provider,
        thread_factory: Callable[..., Any] = threading.Thread,
    ) -> None:
        self._emit = emit
        self._run_provider = run_provider
        self._thread_factory = thread_factory
        self._task: Any = None
        self._cancel_requested = threading.Event()

    def is_running(self) -> bool:
        return bool(self._task and self._task.is_alive())

    def start(self, command: dict[str, Any]) -> None:
        if self.is_running():
            self._emit("error", message="Processing is already running")
            return
        text = str(command.get("text") or "").strip()
        paths = [Path(str(value)) for value in command.get("files", []) if str(value or "").strip()]
        if not text and not paths:
            self._emit("error", message="Provide LLM transcript text or files")
            return
        if any(not path.is_file() for path in paths):
            self._emit("error", message="LLM transcript file does not exist")
            return
        modes = command.get("modes", [command.get("mode") or "summary"])
        if not isinstance(modes, list) or not modes or not all(isinstance(mode, str) for mode in modes):
            self._emit("error", message="Select at least one LLM mode")
            return
        custom_prompt = str(command.get("prompt") or "").strip()
        if "custom" in modes and not custom_prompt:
            self._emit("error", message="Custom LLM prompt is required")
            return
        settings = command.get("settings")
        if not isinstance(settings, dict):
            self._emit("error", message="LLM settings are required")
            return
        transcript = "\n\n".join(
            ([f"--- transcript ---\n{text}"] if text else [])
            + [f"--- {path.name} ---\n{path.read_text(encoding='utf-8')}" for path in paths]
        )
        output_dir = str(command.get("output_dir") or "")
        target = Path(output_dir) if output_dir else (paths[-1].parent if paths else None)
        self._cancel_requested.clear()
        self._task = self._thread_factory(
            target=self._run, args=(transcript, list(dict.fromkeys(modes)), custom_prompt, settings, target), daemon=True
        )
        self._task.start()

    def cancel(self) -> None:
        if not self.is_running():
            self._emit("error", message="No LLM request is running")
            return
        self._cancel_requested.set()

    def _run(self, transcript: str, modes: list[str], custom_prompt: str, settings: dict[str, Any], target: Path | None) -> None:
        saved_files: list[str] = []
        results: list[dict[str, str]] = []
        provider = str(settings.get("provider") or "API")
        try:
            for index, mode in enumerate(modes, 1):
                prompt = custom_prompt if mode == "custom" else PROMPTS.get(mode, custom_prompt)
                if not prompt:
                    raise ValueError(f"Unknown LLM mode: {mode}")
                self._emit("llm_started", mode=mode, index=index, total=len(modes))
                answer = self._run_provider(
                    settings, transcript, prompt, provider=provider, strict_empty_cli=True,
                    on_stream_chunk=lambda chunk, mode=mode: self._emit("llm_chunk", mode=mode, text=chunk),
                    cancel_check=self._cancel_requested.is_set,
                )
                if self._cancel_requested.is_set():
                    raise _Cancelled()
                results.append({"mode": mode, "text": answer})
                if target is not None:
                    target.mkdir(parents=True, exist_ok=True)
                    saved = target / f"session_llm_{mode}.txt"
                    saved.write_text(answer, encoding="utf-8")
                    saved_files.append(str(saved))
            self._emit("llm_completed", success=True, saved_files=saved_files, results=results)
        except _Cancelled:
            self._emit("llm_completed", success=False, cancelled=True, saved_files=saved_files, results=results)
        except Exception as exc:
            if self._cancel_requested.is_set():
                self._emit("llm_completed", success=False, cancelled=True, saved_files=saved_files, results=results)
            else:
                self._emit("llm_completed", success=False, message=str(exc))
