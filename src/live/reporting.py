"""Report a recurring live-path problem once instead of on every chunk."""

from __future__ import annotations

from collections.abc import Hashable


class ReportOnce:
    """Remembers what was already reported, so each key is reported once.

    Problems on per-chunk paths (a failing consumer, a recording write, a
    stalled mix peer, an unavailable speaker estimator) repeat 50-100 times a
    second; issue #48 logged one of them 15 255 times in a session.
    """

    def __init__(self) -> None:
        self._seen: set[Hashable] = set()

    def first(self, key: Hashable) -> bool:
        """True the first time `key` is seen; the caller reports only then."""
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def __contains__(self, key: Hashable) -> bool:
        return key in self._seen
