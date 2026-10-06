"""Best-effort session log so live failures survive a windowed frozen build."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


class SessionLog:
    """Append diagnostics to the session directory without ever raising."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = Lock()
        self._disabled = False

    def write(self, message: str) -> None:
        if self._disabled:
            return
        line = f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} {message}"
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as file:
                    file.write(f"{line}\n")
                    file.flush()
        except Exception:
            # Losing diagnostics must never take the capture pipeline down.
            self._disabled = True
