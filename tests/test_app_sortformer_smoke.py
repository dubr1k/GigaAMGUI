import app
from src.core.diarization import sortformer_onnx


def test_sortformer_onnx_smoke_exercises_processor_mapping_contract(monkeypatch):
    events = []

    class FakeManager:
        def __init__(self, *, provider):
            events.append(("init", provider))

        def prepare(self):
            events.append(("prepare",))

        def smoke_test(self):
            return {
                "frames": 4,
                "speakers": 4,
                "requested_provider": "cuda",
                "session_providers": ["CUDAExecutionProvider"],
            }

        def map_speakers_to_transcription(self, transcription, speakers):
            events.append(("map", transcription, speakers))
            return [{
                "transcription": "тест",
                "boundaries": (0.0, 0.5),
                "speaker": "Спикер №1",
            }]

        def unload(self):
            events.append(("unload",))

    monkeypatch.setattr(sortformer_onnx, "SortformerOnnxDiarizationManager", FakeManager)

    report = app.run_sortformer_onnx_smoke("cuda")

    smoke = report["sortformer_onnx"]
    assert isinstance(smoke, dict)
    assert smoke["session_providers"] == ["CUDAExecutionProvider"]
    assert report["mapping_contract"] == {
        "turns": 1,
        "speakers": ["Спикер №1"],
        "text": "тест",
    }
    assert [event[0] for event in events] == ["init", "prepare", "map", "unload"]


def test_sortformer_onnx_smoke_rejects_broken_mapping_contract(monkeypatch):
    events = []

    class FakeManager:
        def __init__(self, *, provider):
            pass

        def prepare(self):
            pass

        def smoke_test(self):
            return {"session_providers": ["CPUExecutionProvider"]}

        def map_speakers_to_transcription(self, transcription, speakers):
            return []

        def unload(self):
            events.append("unload")

    monkeypatch.setattr(sortformer_onnx, "SortformerOnnxDiarizationManager", FakeManager)

    try:
        app.run_sortformer_onnx_smoke("cpu")
    except RuntimeError as exc:
        assert "mapping contract" in str(exc).lower()
    else:
        raise AssertionError("broken mapping contract must fail the smoke")

    assert events == ["unload"]
