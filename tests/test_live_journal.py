import json
from datetime import datetime

from src.live.journal import EventJournal, LiveSessionStore, default_session_root
from src.live.types import CaptureSource, LiveSettings, TranscriptEvent


def event(event_id: str, revision: int, text: str, status: str = "final") -> TranscriptEvent:
    return TranscriptEvent(
        event_id=event_id,
        revision=revision,
        source=CaptureSource.MIC,
        sample_start=0,
        sample_end=48_000,
        timestamp_ns=1,
        text=text,
        status=status,
        supersedes=revision - 1 if revision else None,
    )


def test_latest_revision_supersedes_prior_event(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    journal.append(event("e1", revision=0, text="hel", status="partial"))
    journal.append(event("e1", revision=1, text="hello"))

    assert [(item.event_id, item.text) for item in journal.latest_events()] == [("e1", "hello")]


def test_journal_remains_append_only_and_recovers_events_after_reopen(tmp_path):
    path = tmp_path / "events.jsonl"
    EventJournal(path).append(event("e1", revision=0, text="one"))
    EventJournal(path).append(event("e2", revision=0, text="two"))

    assert [json.loads(line)["event_id"] for line in path.read_text().splitlines()] == ["e1", "e2"]
    assert [item.text for item in EventJournal(path).latest_events()] == ["one", "two"]


def test_session_store_creates_metadata_and_atomically_replaces_checkpoint(tmp_path):
    session_dir = LiveSessionStore(tmp_path).create(LiveSettings(record_mix_audio=False))
    store = LiveSessionStore(tmp_path)
    store.write_checkpoint(session_dir, {"next_offset": 10})
    store.write_checkpoint(session_dir, {"next_offset": 20})

    assert json.loads((session_dir / "metadata.json").read_text())["record_mix_audio"] is False
    assert json.loads((session_dir / "checkpoint.json").read_text()) == {"next_offset": 20}
    assert not list(session_dir.glob("*.tmp"))


def test_session_folder_is_named_after_its_start_time(tmp_path):
    """`session-9e6564f7…` told the user nothing about which recording it held."""
    started = datetime(2026, 10, 1, 17, 21, 14)
    store = LiveSessionStore(tmp_path, clock=lambda: started)

    first = store.create(LiveSettings())
    second = store.create(LiveSettings())

    assert first.name == "2026-10-01_17-21-14"
    assert second.name == "2026-10-01_17-21-14-2"


def test_default_session_root_is_documents_gigaam_live(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_session_root() == tmp_path / "Documents" / "GigaAM" / "live"


def test_torn_last_line_does_not_hide_the_events_before_it(tmp_path):
    """One torn line made every later read raise, and stop() failed at export."""
    path = tmp_path / "events.jsonl"
    EventJournal(path).append(event("e1", revision=0, text="one"))
    with path.open("a", encoding="utf-8") as file:
        file.write('{"event_id":"e2","revision":0,"source":"mi')

    assert [item.text for item in EventJournal(path).latest_events()] == ["one"]


def test_append_after_a_torn_line_starts_a_new_line(tmp_path):
    path = tmp_path / "events.jsonl"
    EventJournal(path).append(event("e1", revision=0, text="one"))
    with path.open("a", encoding="utf-8") as file:
        file.write('{"event_id":"e2","rev')

    journal = EventJournal(path)
    journal.append(event("e3", revision=0, text="three"))

    assert [item.text for item in EventJournal(path).latest_events()] == ["one", "three"]


def test_invalid_lines_are_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    EventJournal(path).append(event("e1", revision=0, text="one"))
    with path.open("a", encoding="utf-8") as file:
        file.write("not json\n")
        file.write('{"event_id":"e2","source":"radio"}\n')
        file.write("[1, 2]\n")
        file.write("\n")
        file.write('{"event_id":"e3","unexpected":true}\n')
    EventJournal(path).append(event("e4", revision=0, text="four"))

    assert [item.text for item in EventJournal(path).latest_events()] == ["one", "four"]


def test_torn_multibyte_character_does_not_break_the_read(tmp_path):
    path = tmp_path / "events.jsonl"
    EventJournal(path).append(event("e1", revision=0, text="привет"))
    with path.open("ab") as file:
        file.write('{"event_id":"e2","text":"п'.encode()[:-1])

    assert [item.text for item in EventJournal(path).latest_events()] == ["привет"]
