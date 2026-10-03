"""Разрешение входов TUI: реальные файлы, отмена и неблокирующий протокол."""
import io
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest


def resolve(paths, cancelled=lambda: False):
    from src.services.tui_input_service import resolve_paths

    return resolve_paths([str(path) for path in paths], cancelled)


def test_folder_order_dedup_and_missing(tmp_path):
    folder = tmp_path / "записи"
    folder.mkdir()
    (folder / "z.mp3").touch()
    (folder / "a.wav").touch()
    (folder / "notes.json").touch()
    (folder / "nested").mkdir()
    (folder / "nested" / "b.MP4").touch()

    result = resolve([folder, folder / "a.wav", tmp_path / "missing.wav"])

    assert result["files"] == [str((folder / name).resolve()) for name in ("a.wav", "nested/b.MP4", "z.mp3")]
    assert result["duplicates"] == [str((folder / "a.wav").resolve())]
    assert [error["code"] for error in result["errors"]] == ["missing"]
    assert not result["cancelled"]


def test_explicit_input_order_is_preserved(tmp_path):
    paths = [tmp_path / name for name in ("z.wav", "a.mp3")]
    for path in paths:
        path.touch()
    assert resolve(paths)["files"] == [str(path.resolve()) for path in paths]


def symlink_or_skip(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if sys.platform == "win32":
            pytest.skip(f"symlink permission unavailable: {exc}")
        raise


def test_directory_symlink_is_not_followed_and_file_alias_is_deduplicated(tmp_path):
    path = tmp_path / "a.wav"
    path.touch()
    symlink_or_skip(tmp_path / "loop", tmp_path, directory=True)
    symlink_or_skip(tmp_path / "alias.wav", path)

    result = resolve([tmp_path])

    assert result["files"] == [str(path.resolve())]
    assert result["duplicates"] == [str(path.resolve())]
    assert result["errors"] == []


class _FakeEntry:
    """A directory entry with a name the OS returned as non-UTF-8 bytes (Linux).

    Python hands such names over with surrogateescape (`\\udcff`), which no UTF-8
    stream can write: macOS/Windows refuse to create these files, so the entry is
    injected into a real directory listing instead."""

    def __init__(self, directory, name, *, directory_entry=False):
        self.name = name
        self.path = os.path.join(directory, name)
        self._directory = directory_entry

    def is_dir(self, follow_symlinks=True):
        return self._directory

    def is_file(self, follow_symlinks=True):
        return not self._directory


class _Listing:
    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *exc):
        return False


def test_non_utf8_names_in_a_folder_are_reported_and_the_reply_stays_encodable(tmp_path, monkeypatch):
    """A dropped folder with one non-UTF-8 name used to put the surrogate path into
    `errors`; writing that reply raised UnicodeEncodeError in the resolver thread,
    the exception was swallowed and no `inputs_resolved` ever came — Start stayed
    locked behind "adding inputs"."""
    import src.services.tui_input_service as service

    good = tmp_path / "good.wav"
    good.touch()
    real_scandir = os.scandir

    undecodable_folder = os.path.join(str(tmp_path), "dir\udcfe")

    def scandir(path):
        if str(path) == undecodable_folder:  # its media inherit the undecodable path
            return _Listing([_FakeEntry(undecodable_folder, "inside.wav"),
                             _FakeEntry(undecodable_folder, "notes\udcfd.txt")])
        entries = list(real_scandir(path))
        if Path(path) == tmp_path:
            entries.append(_FakeEntry(str(path), "bad\udcff.wav"))
            entries.append(_FakeEntry(str(path), "dir\udcfe", directory_entry=True))
        return _Listing(entries)

    monkeypatch.setattr(service.os, "scandir", scandir)
    result = resolve([tmp_path])

    assert result["files"] == [str(good.resolve())]
    paths = sorted(error["path"] for error in result["errors"])
    assert [error["code"] for error in result["errors"]] == ["undecodable", "undecodable"]
    assert paths == sorted([os.path.join(str(tmp_path), "bad�.wav"),
                            os.path.join(str(tmp_path), "dir�", "inside.wav")])
    json.dumps(result, ensure_ascii=False).encode("utf-8")


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux file systems keep non-UTF-8 names")
def test_real_non_utf8_file_name_is_reported_not_swallowed(tmp_path):
    (tmp_path / "good.wav").touch()
    open(os.path.join(os.fsencode(tmp_path), b"bad\xff.wav"), "wb").close()
    result = resolve([tmp_path])
    assert result["files"] == [str((tmp_path / "good.wav").resolve())]
    assert [error["code"] for error in result["errors"]] == ["undecodable"]
    json.dumps(result, ensure_ascii=False).encode("utf-8")


def test_resolver_always_replies_even_if_the_result_cannot_be_written(tmp_path):
    from src.services.tui_input_service import InputResolver

    path = tmp_path / "a.wav"
    path.touch()
    messages = queue.Queue()
    failures = []

    def emit(kind, **payload):
        if not failures:
            failures.append(kind)
            raise UnicodeEncodeError("utf-8", "\udcff", 0, 1, "surrogates not allowed")
        messages.put({"type": kind, **payload})

    resolver = InputResolver(emit)
    try:
        resolver.start({"request_id": 3, "paths": [str(path)]})
        message = messages.get(timeout=5)
    finally:
        resolver.close()
    assert message["type"] == "inputs_resolved"
    assert message["request_id"] == 3
    assert message["files"] == []
    assert message["errors"][0]["code"] == "internal"


def test_a_reply_too_large_for_the_protocol_is_an_error_not_a_dead_worker(tmp_path, monkeypatch):
    """Clients cap one protocol line at 8 MiB and kill a worker that exceeds it; a
    drop of a huge folder tree produced exactly such an `inputs_resolved`."""
    import src.services.tui_input_service as service

    for index in range(30):
        (tmp_path / f"{index:02}.wav").touch()
    monkeypatch.setattr(service, "MAX_REPLY_BYTES", 1000)
    result = resolve([tmp_path])
    assert result["files"] == [] and result["duplicates"] == []
    assert [error["code"] for error in result["errors"]] == ["too_many_files"]
    assert "30" in result["errors"][0]["message"]


def test_many_errors_are_summarized(tmp_path):
    import src.services.tui_input_service as service

    paths = [tmp_path / f"missing-{index}.wav" for index in range(250)]
    result = resolve(paths)
    assert len(result["errors"]) == service.MAX_REPORTED_ERRORS + 1
    assert result["errors"][-1]["code"] == "more_errors"
    assert str(250 - service.MAX_REPORTED_ERRORS) in result["errors"][-1]["message"]


def test_unreadable_subfolder_does_not_discard_other_files(tmp_path, monkeypatch):
    import src.services.tui_input_service as service

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    good = tmp_path / "good.wav"
    good.touch()
    real_scandir = os.scandir

    def scandir(path):
        if Path(path) == blocked:
            raise PermissionError("test denied")
        return real_scandir(path)

    monkeypatch.setattr(service.os, "scandir", scandir)
    result = resolve([tmp_path])
    assert result["files"] == [str(good.resolve())]
    assert [error["code"] for error in result["errors"]] == ["unreadable"]
    assert "test denied" in result["errors"][0]["message"]


@pytest.mark.parametrize("name", ["Ректорат (1)\u00a0— копия.MP3", "кавычки 'и' пробелы.wav", "🦄.m4a"])
@pytest.mark.parametrize("representation", ["path", "uri", "home"])
def test_unicode_urls_and_home_keep_the_exact_filename(tmp_path, monkeypatch, name, representation):
    path = tmp_path / name
    path.touch()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    raw = {"path": str(path), "uri": path.as_uri(), "home": f"~/{name}"}[representation]
    result = resolve([raw])
    assert result["files"] == [str(path.resolve())]
    assert result["errors"] == []


def test_reports_empty_folder_and_unsupported_explicit_file(tmp_path):
    other = tmp_path / "notes.json"
    other.touch()
    result = resolve([tmp_path, other])
    assert result["files"] == []
    assert [error["code"] for error in result["errors"]] == ["empty_directory", "unsupported"]


def test_remote_file_uri_is_not_treated_as_local(tmp_path):
    path = tmp_path / "a.wav"
    path.touch()
    result = resolve([path.as_uri().replace("file://", "file://remote-host")])
    assert result["files"] == []
    assert result["errors"][0]["code"] == "invalid_request"


def test_cancelled_scan_discards_partial_results(tmp_path):
    first = tmp_path / "a.wav"
    first.touch()
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 2

    result = resolve([first, first, first], cancelled)
    assert result["cancelled"]
    assert result["files"] == []
    assert result["duplicates"] == []


def test_resolver_uses_the_shared_extension_registry(tmp_path, monkeypatch):
    import src.config as config

    path = tmp_path / "future.newmedia"
    path.touch()
    monkeypatch.setattr(config, "MEDIA_EXTENSIONS", (*config.MEDIA_EXTENSIONS, ".newmedia"))
    assert resolve([path])["files"] == [str(path.resolve())]


@pytest.mark.parametrize("command", [
    {"request_id": True, "paths": ["/a.wav"]},
    {"request_id": 0, "paths": ["/a.wav"]},
    {"request_id": [], "paths": ["/a.wav"]},
    {"request_id": 1, "paths": "not-an-array"},
    {"request_id": 1, "paths": []},
    {"request_id": 1, "paths": [None]},
    {"request_id": 1, "paths": [" "]},
])
def test_invalid_resolve_request_has_a_correlated_error(command):
    from src.services.tui_input_service import InputResolver

    messages = queue.Queue()
    resolver = InputResolver(lambda kind, **payload: messages.put({"type": kind, **payload}))
    try:
        resolver.start(command)
        message = messages.get(timeout=2)
        assert message["type"] == "inputs_resolved"
        assert message["request_id"] == command["request_id"]
        assert message["files"] == []
        assert message["errors"][0]["code"] == "invalid_request"
    finally:
        resolver.close()


def test_worker_pings_during_serial_resolves_and_cancel_is_per_request(tmp_path, monkeypatch):
    import src.services.tui_input_service as service
    from src.tui_worker import TuiWorker

    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    second_finished = threading.Event()
    real_resolve = service.resolve_paths
    path = tmp_path / "a.wav"
    path.touch()

    def slow_resolve(paths, cancelled):
        if paths == ["slow"]:
            first_entered.set()
            assert release_first.wait(3)
        else:
            second_entered.set()
        return real_resolve(paths, cancelled)

    monkeypatch.setattr(service, "resolve_paths", slow_resolve)
    class Output(io.StringIO):
        def write(self, text):
            count = super().write(text)
            message = json.loads(text)
            if message.get("type") == "inputs_resolved" and message.get("request_id") == 2:
                second_finished.set()
            return count

    output = Output()
    worker = TuiWorker(output=output)
    try:
        worker.handle({"type": "resolve_inputs", "request_id": 1, "paths": ["slow"]})
        assert first_entered.wait(2)
        worker.handle({"type": "resolve_inputs", "request_id": 2, "paths": [str(path)]})
        worker.handle({"type": "ping"})
        assert json.loads(output.getvalue()) == {"type": "pong"}
        assert not second_entered.is_set()
        worker.handle({"type": "cancel_inputs", "request_id": 1})
        worker.handle({"type": "cancel_inputs", "request_id": 999})
        release_first.set()
        assert second_entered.wait(2)
        assert second_finished.wait(2)
    finally:
        release_first.set()
        worker.close()
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    first, second = messages[1:]
    assert first["request_id"] == 1 and first["cancelled"] and not first["files"]
    assert second["request_id"] == 2 and not second["cancelled"]
    assert second["files"] == [str(path.resolve())]


def test_resolution_does_not_import_machine_learning_modules(tmp_path):
    path = tmp_path / "a.wav"
    path.touch()
    script = """
import io, json, sys, threading
from src.tui_worker import TuiWorker
worker = TuiWorker(output=io.StringIO())
done = threading.Event()
emit = worker.emit
def capture(kind, **payload):
    emit(kind, **payload)
    if kind == 'inputs_resolved':
        done.set()
worker._inputs._emit = capture
try:
    worker.handle({'type': 'resolve_inputs', 'request_id': 1, 'paths': [sys.argv[1]]})
    assert done.wait(5)
    worker.handle({'type': 'ping'})
    assert not any(name in sys.modules for name in ('torch', 'gigaam', 'pyannote.audio'))
    print(worker._output.getvalue())
finally:
    worker.close()
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    messages = [json.loads(line) for line in result.stdout.splitlines() if line]
    assert messages[0]["files"] == [str(path.resolve())]
    assert messages[1] == {"type": "pong"}
