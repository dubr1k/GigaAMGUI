import base64
import io
import json
from array import array
from pathlib import Path

import pytest

from scripts import native_worker_smoke

from scripts.native_worker_smoke import pcm_chunks


def test_pcm_chunks_are_100ms_int16_base64_with_contiguous_seq():
    samples = array("h", range(-4000, 4000))  # 0.5 s at 16 kHz

    chunks = list(pcm_chunks(samples, 16_000))

    assert [c["seq"] for c in chunks] == list(range(len(chunks)))
    assert [c["sample_offset"] for c in chunks][:3] == [0, 1600, 3200]
    assert all(c["type"] == "live_audio" and c["source"] == "mic" for c in chunks)
    first = base64.b64decode(chunks[0]["pcm"])
    assert len(first) == 1600 * 2
    assert int.from_bytes(first[:2], "little", signed=True) == -4000
    assert chunks[1]["timestamp_ns"] == 100_000_000


def test_pcm_chunks_can_continue_a_sequence():
    tail = array("h", bytes(3200 * 2))

    chunks = list(pcm_chunks(tail, 16_000, first_seq=7, first_offset=11_200))

    assert [c["seq"] for c in chunks] == [7, 8]
    assert [c["sample_offset"] for c in chunks] == [11_200, 12_800]


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("selected", [None, "v4_rnnt", "multilingual_v2_rnnt", "multilingual_ctc"])
def test_cli_forwards_selected_model_and_preserves_default(tmp_path, monkeypatch, live, selected):
    companion = tmp_path / "worker"
    clip = tmp_path / "clip.wav"
    companion.touch()
    clip.touch()
    monkeypatch.setattr(native_worker_smoke.os, "access", lambda *_args: True)
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(native_worker_smoke, "run_smoke", run)
    monkeypatch.setattr(native_worker_smoke, "run_live_smoke", run)
    argv = [str(companion), str(clip), str(tmp_path / "output")]
    if live:
        argv.append("--live")
    if selected:
        argv.extend(["--model", selected])
    assert native_worker_smoke.main(argv) == 0
    assert calls[0]["model"] == (selected or "v3_e2e_rnnt")


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("model", ["v4_rnnt", "multilingual_v2_rnnt", "multilingual_ctc"])
def test_worker_start_message_contains_requested_model(tmp_path, monkeypatch, live, model):
    class RecordingInput(io.StringIO):
        def close(self):
            self.sent = self.getvalue()
            super().close()

    class FakeProcess:
        def __init__(self, events):
            self.stdin = RecordingInput()
            self.stdout = io.StringIO("".join(json.dumps(event) + "\n" for event in events))

        def wait(self, timeout):
            return 0

        def kill(self):
            raise AssertionError("worker should have exited")

    clip = Path("tests/fixtures/liquid_smoke.wav").resolve()
    output = tmp_path / "output"
    output.mkdir()
    if live:
        transcript = output / "transcript.txt"
        transcript.write_text("speech", encoding="utf-8")
        events = [
            {"type": "live_final", "text": "speech"},
            {"type": "live_stopped", "saved_files": [str(transcript)]},
        ]
    else:
        (output / f"{clip.stem}.txt").write_text("speech", encoding="utf-8")
        events = [{"type": "completed", "success": True}]
    process = FakeProcess(events)
    monkeypatch.setattr(native_worker_smoke.subprocess, "Popen", lambda *_args, **_kwargs: process)
    run = native_worker_smoke.run_live_smoke if live else native_worker_smoke.run_smoke
    assert run(tmp_path / "worker", clip, output, model=model) == 0
    start = json.loads(process.stdin.sent.splitlines()[0])
    assert start["type"] == ("live_start" if live else "start")
    assert start["model"] == model
