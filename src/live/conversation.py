"""Assistant questions asked during a live session, kept next to its transcript."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Lock

from .journal import ConversationJournal


@dataclass(frozen=True)
class ConversationTurn:
    id: str
    question: str
    answer: str = ""
    status: str = "generating"


class ConversationLog:
    """The session's question/answer turns; frozen once the session stops.

    Answers stream in from an LLM thread while the session records, so every
    method takes the log's own lock. A finished turn is appended to
    conversation.jsonl; a turn still generating at stop is journaled as
    "frozen" so the file never ends on an unanswered question silently.
    """

    def __init__(self, journal: ConversationJournal) -> None:
        self._journal = journal
        self._turns: list[ConversationTurn] = []
        self._frozen = False
        self._lock = Lock()

    def begin(self, question: str) -> ConversationTurn:
        with self._lock:
            self._require_open()
            turn = ConversationTurn(f"conversation-{len(self._turns)}", question)
            self._turns.append(turn)
            return turn

    def append_answer(self, turn_id: str, text: str) -> None:
        with self._lock:
            self._require_open()
            turn = self._turn(turn_id)
            self._replace(replace(turn, answer=turn.answer + text))

    def finish(self, turn_id: str, answer: str | None = None, *, status: str = "complete") -> None:
        with self._lock:
            self._require_open()
            turn = self._turn(turn_id)
            turn = replace(turn, answer=turn.answer if answer is None else answer, status=status)
            self._replace(turn)
            self._journal.append(turn)

    def clear(self) -> None:
        with self._lock:
            self._require_open()
            self._turns.clear()
            self._journal.clear()

    def turns(self) -> list[ConversationTurn]:
        with self._lock:
            return list(self._turns)

    def freeze(self) -> None:
        with self._lock:
            if self._frozen:
                return
            for turn in self._turns:
                if turn.status == "generating":
                    self._journal.append(replace(turn, status="frozen"))
            self._frozen = True

    def _require_open(self) -> None:
        if self._frozen:
            raise RuntimeError("conversation is frozen")

    def _turn(self, turn_id: str) -> ConversationTurn:
        for turn in self._turns:
            if turn.id == turn_id:
                return turn
        raise KeyError(turn_id)

    def _replace(self, updated: ConversationTurn) -> None:
        self._turns = [updated if turn.id == updated.id else turn for turn in self._turns]
