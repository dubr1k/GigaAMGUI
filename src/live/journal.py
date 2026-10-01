"""Recoverable session metadata and append-only transcript revisions."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.utils.atomic_json import save_json_atomic

from .types import CaptureSource, LiveSettings, TranscriptEvent


def default_session_root() -> Path:
    """Where every front-end keeps live sessions unless the user picks a folder."""
    return Path.home() / "Documents" / "GigaAM" / "live"


class LiveSessionStore:
    def __init__(self, root_dir: Path, *, clock: Callable[[], datetime] = datetime.now) -> None:
        self._root_dir = Path(root_dir)
        self._clock = clock

    def create(self, settings: LiveSettings) -> Path:
        """Make the session folder, named by its local start time.

        A `session-<uuid>` name gave no hint which recording a folder held; a
        second session within the same second gets a numeric suffix.
        """
        self._root_dir.mkdir(parents=True, exist_ok=True)
        stem = self._clock().strftime("%Y-%m-%d_%H-%M-%S")
        session_dir = self._root_dir / stem
        suffix = 1
        while True:
            try:
                session_dir.mkdir()
                break
            except FileExistsError:
                suffix += 1
                session_dir = self._root_dir / f"{stem}-{suffix}"
        metadata = asdict(settings)
        metadata["diarization_mode"] = settings.diarization_mode.value
        save_json_atomic(str(session_dir / "metadata.json"), metadata)
        return session_dir

    def write_checkpoint(self, session_dir: Path, checkpoint: dict) -> None:
        save_json_atomic(str(Path(session_dir) / "checkpoint.json"), checkpoint)

    def update_metadata(self, session_dir: Path, **values: object) -> None:
        path = Path(session_dir) / "metadata.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata.update(values)
        save_json_atomic(str(path), metadata)


class EventJournal:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def append(self, event: TranscriptEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(event)
        payload.pop("source_label", None)
        payload["source"] = event.source.value
        with self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")
            file.flush()

    def latest_events(self) -> list[TranscriptEvent]:
        latest: dict[str, TranscriptEvent] = {}
        if not self._path.exists():
            return []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            data["source"] = CaptureSource(data["source"])
            event = TranscriptEvent(**data)
            prior = latest.get(event.event_id)
            if prior is None or event.revision >= prior.revision:
                latest[event.event_id] = event
        return list(latest.values())


class ConversationJournal:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def append(self, turn) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": turn.id,
            "question": turn.question,
            "answer": turn.answer,
            "status": turn.status,
        }
        with self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")
            file.flush()

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
