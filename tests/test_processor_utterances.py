"""process_file must hand utterances back to callers (the API builds responses from them)."""
from unittest.mock import MagicMock, patch

from src.core.processor import TranscriptionProcessor


def _fake_utterances():
    return [
        {"transcription": "привет", "boundaries": (0.0, 1.0)},
        {"transcription": "мир", "boundaries": (1.0, 2.0), "words": [{"text": "мир", "start": 1.0, "end": 2.0}]},
    ]


def test_process_file_returns_utterances(tmp_path):
    loader = MagicMock()
    loader.transcribe_longform.return_value = _fake_utterances()
    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF")

    # AudioConverter is instantiated inside TranscriptionProcessor.__init__, so it must
    # be patched before construction; mode="off" (default) skips the preprocessor's ffmpeg
    # path entirely, so no further mocking is needed to reach transcribe_longform.
    with patch("src.core.processor.AudioConverter") as conv:
        conv.get_media_duration.return_value = 2.0
        conv.return_value.convert_to_wav.return_value = str(src)
        processor = TranscriptionProcessor(loader, MagicMock(), logger=lambda *_: None)
        result = processor.process_file(str(src), str(tmp_path), 0, 1, "a.wav", output_formats=[])

    assert result["success"] is True
    assert result["utterances"] == _fake_utterances()
    assert result["saved_files"] == []
