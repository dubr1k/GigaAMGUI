from src.live.exports import ExportSelection, export_session
from src.live.types import CaptureSource, TranscriptEvent


def event(
    event_id: str,
    revision: int,
    text: str,
    status: str = "final",
    sample_start: int = 0,
) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=event_id,
        revision=revision,
        source=CaptureSource.MIC,
        sample_start=sample_start,
        sample_end=sample_start + 48_000,
        timestamp_ns=1,
        text=text,
        status=status,
        speaker="Speaker 1",
        supersedes=revision - 1 if revision else None,
    )


def test_export_is_atomic_and_uses_only_latest_final_events(tmp_path):
    paths = export_session(
        tmp_path,
        [event("one", 0, "draft"), event("one", 1, "one"), event("two", 0, "two", "partial")],
        ExportSelection(txt=True),
    )

    assert paths == [tmp_path / "transcript.txt"]
    assert paths[0].read_text(encoding="utf-8") == "one\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_subtitle_exports_sort_revisions_and_renumber_cues(tmp_path):
    # Пауза больше _MAX_JOIN_GAP_SECONDS: реплики остаются отдельными cue,
    # поэтому нумерация и порядок по sample_start реально проверяются.
    paths = export_session(
        tmp_path,
        [event("second", 0, "second", sample_start=144_000), event("first", 0, "old"), event("first", 1, "first")],
        ExportSelection(srt=True, vtt=True),
    )

    srt, vtt = (path.read_text(encoding="utf-8") for path in paths)
    # Метка спикера печатается на первом cue реплики: `<Имя>` не тег SRT.
    assert "1\n00:00:00,000 --> 00:00:01,000\nSpeaker 1: MIC: first" in srt
    assert "2\n00:00:03,000 --> 00:00:04,000\nMIC: second" in srt
    assert "old" not in srt
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:01.000\n<v Speaker 1>MIC: first" in vtt
    assert "00:00:03.000 --> 00:00:04.000\n<v Speaker 1>MIC: second" in vtt


def test_subtitle_exports_join_one_speaker_across_a_short_pause(tmp_path):
    # Пауза в пределах _MAX_JOIN_GAP_SECONDS у того же спикера склеивается в один
    # cue — это поведение субтитров из #43, и live-экспорт обязан ему следовать.
    paths = export_session(
        tmp_path,
        [event("first", 0, "first"), event("second", 0, "second", sample_start=96_000)],
        ExportSelection(srt=True),
    )

    assert paths[0].read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:03,000\nSpeaker 1: MIC: first MIC: second\n"
    )


def test_live_exports_materialize_each_selected_processing_format(tmp_path):
    paths = export_session(
        tmp_path,
        [event("one", 0, "one")],
        ExportSelection(
            txt=True,
            txt_timecodes=True,
            txt_diarize=True,
            txt_diarize_timecodes=True,
            md=True,
            srt=True,
            vtt=True,
        ),
    )

    assert [path.name for path in paths] == [
        "transcript.txt",
        "transcript_timecodes.txt",
        "transcript_diarize.txt",
        "transcript_diarize_timecodes.txt",
        "transcript.md",
        "transcript.srt",
        "transcript.vtt",
    ]
    assert paths[0].read_text(encoding="utf-8") == "one\n"
    assert paths[1].read_text(encoding="utf-8") == "[00:00.000] one\n"
    assert paths[2].read_text(encoding="utf-8") == "Speaker 1: one\n"
    assert paths[3].read_text(encoding="utf-8") == "[00:00.000] Speaker 1: one\n"
    assert "# Транскрипция: Live transcript" in paths[4].read_text(encoding="utf-8")
