"""Recoverable session metadata and append-only transcript revisions."""

from __future__ import annotations

import json
import threading
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
    """Append-only transcript revisions that survive a torn write.

    A crash or a full disk can leave the last line half-written. Reading used
    to parse every line strictly, so one torn line made every later read
    raise — including the one stop() exports from — and the next append was
    glued onto the fragment, losing that event too.

    The latest revision of each event is kept in memory once the file has
    been read: the session asks for it on every final (speaker estimates)
    and for every assistant question, and re-parsing the whole file under
    the session lock each time grew with the session.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._tail_checked = False
        self._lock = threading.Lock()
        self._latest: dict[str, TranscriptEvent] | None = None

    def append(self, event: TranscriptEvent) -> None:
        payload = asdict(event)
        payload.pop("source_label", None)
        payload["source"] = event.source.value
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            latest = self._loaded()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            prefix = "" if self._tail_checked or self._ends_with_newline() else "\n"
            self._tail_checked = False
            with self._path.open("a", encoding="utf-8") as file:
                file.write(prefix + line)
                file.flush()
            # Only a write that completed leaves the file ending in a newline.
            self._tail_checked = True
            _keep_latest(latest, event)

    def latest_events(self) -> list[TranscriptEvent]:
        with self._lock:
            return list(self._loaded().values())

    def _loaded(self) -> dict[str, TranscriptEvent]:
        if self._latest is None:
            self._latest = {}
            if self._path.exists():
                for line in self._path.read_text(encoding="utf-8", errors="replace").splitlines():
                    event = _parse_event(line)
                    if event is not None:
                        _keep_latest(self._latest, event)
        return self._latest

    def _ends_with_newline(self) -> bool:
        try:
            with self._path.open("rb") as file:
                file.seek(0, 2)
                if file.tell() == 0:
                    return True
                file.seek(-1, 2)
                return file.read(1) == b"\n"
        except FileNotFoundError:
            return True


def _keep_latest(latest: dict[str, TranscriptEvent], event: TranscriptEvent) -> None:
    prior = latest.get(event.event_id)
    if prior is None or event.revision >= prior.revision:
        latest[event.event_id] = event


def _parse_event(line: str) -> TranscriptEvent | None:
    """A journal line as an event, or None when it is torn or not an event."""
    if not line.strip():
        return None
    try:
        data = json.loads(line)
        data["source"] = CaptureSource(data["source"])
        return TranscriptEvent(**data)
    except (ValueError, TypeError, KeyError):
        return None


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
