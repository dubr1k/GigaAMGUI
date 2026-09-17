import base64
from array import array

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
