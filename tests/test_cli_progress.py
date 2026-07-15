from cli import process_files_with_progress
from src.core.progress import ProgressEvent


class FakeProgress:
    def __init__(self):
        self.tasks = []
        self.updates = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def add_task(self, *_, **__):
        self.tasks.append(__)
        return len(self.tasks) - 1

    def update(self, task_id: int, **kwargs):
        self.updates.append((task_id, kwargs))


class FakeProcessor:
    def __init__(self, model_loader, stats_manager, logger, progress_callback=None):
        self.progress_callback = progress_callback

    def process_file(
        self,
        filepath,
        output_dir,
        file_index,
        total_files,
        original_filename=None,
        estimated_conversion_ratio=0.05,
        estimated_transcription_ratio=0.95,
        enable_diarization=False,
        diarization_backend="pyannote",
        num_speakers=None,
        output_formats=None,
    ):
        assert diarization_backend == "pyannote"
        if self.progress_callback:
            self.progress_callback(ProgressEvent(
                stage="transcription",
                stage_progress=0.5,
                file_progress=0.5,
                processed_seconds=5.0,
                total_seconds=10.0,
            ))
            self.progress_callback(
                {
                    "stage": "diarization",
                    "stage_progress": None,
                    "file_progress": 0.7,
                    "processed_seconds": 3.0,
                    "total_seconds": 10.0,
                }
            )
        return {
            "success": True,
            "media_duration": 10.0,
            "file_path": filepath,
            "file_size": 1024,
            "total_time": 1.0,
            "conversion_time": 0.2,
            "transcription_time": 0.8,
            "saved_files": [],
        }


class FakeStats:
    def estimate_processing_time(self, filepath, media_duration):
        return 0.0

    def add_processing_record(self, *_, **__):
        return None


class FakeLogger:
    def debug(self, *_args, **_kwargs):
        return None


class FakeModelLoader:
    def load_model(self, *_args, **_kwargs):
        return True


def test_process_files_with_progress_maps_event_payload_to_rich(monkeypatch):
    fake_progress = FakeProgress()
    monkeypatch.setattr("cli.Progress", lambda *_, **__: fake_progress)
    monkeypatch.setattr("cli.transcription_service.build_processor", FakeProcessor)

    process_files_with_progress(
        ["/tmp/a.wav"],
        "/tmp",
        FakeModelLoader(),
        FakeStats(),
        FakeLogger(),
    )

    # 0=main task, 1=current file task
    main_updates = [update for tid, update in fake_progress.updates if tid == 0]
    file_updates = [update for tid, update in fake_progress.updates if tid == 1]

    assert any(update.get("completed") == 50 for update in file_updates)
    assert any(update.get("total") == 100 for update in file_updates)
    assert any(update.get("completed") == 50 for update in main_updates)


def test_process_files_with_progress_marks_indeterminate_when_stage_progress_is_none(monkeypatch):
    fake_progress = FakeProgress()
    monkeypatch.setattr("cli.Progress", lambda *_, **__: fake_progress)
    monkeypatch.setattr("cli.transcription_service.build_processor", FakeProcessor)

    process_files_with_progress(
        ["/tmp/a.wav", "/tmp/b.wav"],
        "/tmp",
        FakeModelLoader(),
        FakeStats(),
        FakeLogger(),
    )

    file_updates = [update for tid, update in fake_progress.updates if tid == 1]

    assert any(update.get("total") is None for update in file_updates)
    assert any(update.get("completed") == 70 for update in file_updates)
    assert any(update.get("completed") == 50 for update in file_updates)


def test_failed_file_keeps_partial_weight_in_batch_progress(monkeypatch):
    class PartiallyFailingProcessor(FakeProcessor):
        def process_file(self, filepath, *args, **kwargs):
            if filepath.endswith("a.wav"):
                self.progress_callback(ProgressEvent("transcription", 0.4, 0.4))
                return {
                    "success": False,
                    "media_duration": 10.0,
                    "file_path": filepath,
                    "file_size": 1,
                    "total_time": 1.0,
                    "conversion_time": 0.1,
                    "transcription_time": 0.9,
                    "saved_files": [],
                }
            self.progress_callback(ProgressEvent("conversion", 0.2, 0.2))
            return {
                "success": True,
                "media_duration": 10.0,
                "file_path": filepath,
                "file_size": 1,
                "total_time": 1.0,
                "conversion_time": 0.1,
                "transcription_time": 0.9,
                "saved_files": [],
            }

    fake_progress = FakeProgress()
    monkeypatch.setattr("cli.Progress", lambda *_, **__: fake_progress)
    monkeypatch.setattr("cli.transcription_service.build_processor", PartiallyFailingProcessor)

    process_files_with_progress(
        ["/tmp/a.wav", "/tmp/b.wav"],
        "/tmp",
        FakeModelLoader(),
        FakeStats(),
        FakeLogger(),
    )

    main_values = [update["completed"] for tid, update in fake_progress.updates if tid == 0]
    assert 30 in main_values
