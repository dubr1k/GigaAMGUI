"""mcp_backend: the transcription/summarisation path shared by the REST API and MCP.

Processor, model loader, downloader and LLM provider are fakes — no hardware, no network.
"""
import asyncio
import base64
import threading
from pathlib import Path

import pytest

from src.services import cli_tools, llm_service, mcp_backend, transcription_service
from src.services.llm_worker_service import PROMPTS
from src.services.mcp_backend import (
    BackendError,
    LocalBackend,
    TranscribeOptions,
    prepare_options,
    render_result,
    run_transcription,
)
from src.utils.media_downloader import DownloadResult

UTTS = [
    {"transcription": "Привет,", "boundaries": (0.0, 1.5), "speaker": "SPEAKER_01"},
    {"transcription": "как дела?", "boundaries": (1.5, 3.25), "speaker": "SPEAKER_00",
     "words": [{"text": "как", "start": 1.5, "end": 2.0}, {"text": "дела?", "start": 2.0, "end": 3.25}]},
]


class _FakeModelLoader:
    requested_backend = "auto"
    requested_model = "v3_e2e_rnnt"
    requested_provider = "auto"

    def load_model(self, logger=None):
        return True

    def is_loaded(self):
        return True

    def diagnostics(self):
        return {"requested_backend": "auto", "active_backend": "mlx", "model": "v3_e2e_rnnt", "device": "mps"}


class _OwnedLoader(_FakeModelLoader):
    instances: list = []

    def __init__(self, *_, **kw):
        self.kw = kw
        self.unloaded = 0
        _OwnedLoader.instances.append(self)

    def unload(self):
        self.unloaded += 1


class _FakeProcessor:
    calls: list = []
    fail = False

    def __init__(self, *_, **kw):
        self.progress_callback = kw.get("progress_callback")

    def process_file(self, filepath, output_dir, *_a, **kw):
        _FakeProcessor.calls.append({"filepath": filepath, "output_dir": output_dir, **kw})
        if self.progress_callback:
            self.progress_callback("transcription", 0.5, total_seconds=3.25, processed_seconds=1.6)
        if _FakeProcessor.fail:
            return {"success": False}
        return {"success": True, "media_duration": 3.25, "total_time": 0.1, "utterances": list(UTTS),
                "diarization": {"requested": bool(kw.get("enable_diarization")), "applied": bool(kw.get("enable_diarization"))}}


class _FakeDownloader:
    def __init__(self):
        self.calls = []

    def download(self, url, target_dir, progress_callback=None, **kw):
        self.calls.append((url, Path(target_dir), kw))
        target = Path(target_dir) / "downloaded.wav"
        target.write_bytes(b"RIFF")
        if progress_callback:
            progress_callback(50)
        return DownloadResult(files=[str(target)])


@pytest.fixture
def fake_processor(monkeypatch):
    _FakeProcessor.calls = []
    _FakeProcessor.fail = False
    monkeypatch.setattr(transcription_service, "build_processor", lambda *a, **kw: _FakeProcessor(*a, **kw))
    return _FakeProcessor


@pytest.fixture
def upload_dir(tmp_path):
    d = tmp_path / "uploads"
    d.mkdir()
    return d


def _backend(upload_dir, tmp_path, **kw):
    _OwnedLoader.instances = []
    params = dict(
        model_loader=_FakeModelLoader(), stats_manager=None, semaphore=asyncio.Semaphore(1),
        upload_dir=upload_dir, media_downloader=_FakeDownloader(), loader_factory=_OwnedLoader,
        logger=None, http_mode=False, allow_paths=False, path_root=None,
        max_file_size=10_000, max_inline_bytes=1000, hf_token="hf_dummy", llm_config_dir=tmp_path / "cfg",
        max_concurrent=1,
    )
    params.update(kw)
    return LocalBackend(**params)


@pytest.fixture
def backend(upload_dir, tmp_path, fake_processor):
    return _backend(upload_dir, tmp_path)


def _run(coro):
    return asyncio.run(coro)


def _wav(tmp_path, name="clip.wav"):
    p = tmp_path / name
    p.write_bytes(b"RIFF" * 4)
    return p


def _err(coro) -> BackendError:
    with pytest.raises(BackendError) as info:
        _run(coro)
    return info.value


def _leftovers(upload_dir):
    return sorted(p.name for p in upload_dir.glob("mcp_*"))


# ---------- sources ----------


def test_path_source(backend, upload_dir, tmp_path, fake_processor):
    src = _wav(tmp_path)
    result = _run(backend.transcribe(path=str(src), opts=TranscribeOptions(), progress=None))
    assert result["text"] == "Привет, как дела?"
    assert result["duration"] == 3.25 and result["usage"] == {"type": "duration", "seconds": 4}
    assert result["language"] == "ru"
    assert result["source"] == {"kind": "path", "name": "clip.wav"}
    call = fake_processor.calls[0]
    assert call["filepath"] == str(src) and call["output_formats"] == []
    assert Path(call["output_dir"]).parent == upload_dir  # scratch dir lives under upload_dir …
    assert _leftovers(upload_dir) == []  # … and is gone afterwards


def test_url_source_downloads_into_work_dir_and_reports_progress(backend, upload_dir, fake_processor):
    events = []
    result = _run(backend.transcribe(url="https://example.org/a.mp4", opts=TranscribeOptions(),
                                     progress=lambda stage, value: events.append((stage, value))))
    assert result["source"] == {"kind": "url", "name": "downloaded.wav"}
    assert events[0] == ("downloading", None) and events[1] == ("downloading", 0.5)
    assert ("transcription", 0.5) in events
    url, target_dir, kw = backend.media_downloader.calls[0]
    assert url == "https://example.org/a.mp4" and target_dir.parent == upload_dir
    assert kw == {"max_filesize": 10_000}  # yt-dlp пропускает файлы больше MAX_FILE_SIZE
    assert fake_processor.calls[0]["filepath"].endswith("downloaded.wav")
    assert _leftovers(upload_dir) == []


def test_url_download_failure_maps_to_download_failed(backend, upload_dir):
    def boom(url, target_dir, progress_callback=None, **_):
        raise RuntimeError("yt-dlp said no")
    backend.media_downloader.download = boom
    err = _err(backend.transcribe(url="https://example.org/x", opts=TranscribeOptions(), progress=None))
    assert err.code == "download_failed" and err.status == 502
    assert _leftovers(upload_dir) == []
    # max_filesize превышен: yt-dlp молча ничего не скачивает
    backend.media_downloader.download = lambda *a, **kw: DownloadResult(files=[])
    err = _err(backend.transcribe(url="https://example.org/x", opts=TranscribeOptions(), progress=None))
    assert err.code == "download_failed"
    assert _leftovers(upload_dir) == []


def test_inline_source_is_decoded_under_a_safe_name(backend, fake_processor):
    payload = base64.b64encode(b"ID3 fake").decode()
    result = _run(backend.transcribe(audio_base64=payload, filename="../../evil/x.mp3",
                                     opts=TranscribeOptions(), progress=None))
    assert result["source"] == {"kind": "inline", "name": "x.mp3"}
    written = Path(fake_processor.calls[0]["filepath"])
    assert written.name == "x.mp3" and written.parent.name.startswith("mcp_")


def test_inline_source_validation(backend):
    payload = base64.b64encode(b"data").decode()
    assert _err(backend.transcribe(audio_base64=payload, opts=TranscribeOptions(), progress=None)).code == "invalid_request"
    err = _err(backend.transcribe(audio_base64=payload, filename="notes.txt", opts=TranscribeOptions(), progress=None))
    assert err.code == "unsupported_file" and err.status == 400
    err = _err(backend.transcribe(audio_base64="not base64!!", filename="a.mp3", opts=TranscribeOptions(), progress=None))
    assert err.code == "invalid_request"


def test_inline_source_too_large(backend, upload_dir):
    payload = base64.b64encode(b"x" * 1001).decode()
    err = _err(backend.transcribe(audio_base64=payload, filename="a.mp3", opts=TranscribeOptions(), progress=None))
    assert err.code == "file_too_large" and err.status == 413
    assert _leftovers(upload_dir) == []


def test_exactly_one_source_is_required(backend, tmp_path):
    src = _wav(tmp_path)
    assert _err(backend.transcribe(opts=TranscribeOptions(), progress=None)).code == "invalid_request"
    err = _err(backend.transcribe(path=str(src), url="https://x", opts=TranscribeOptions(), progress=None))
    assert err.code == "invalid_request"


def test_path_policy(upload_dir, tmp_path, fake_processor):
    src = _wav(tmp_path)
    http = _backend(upload_dir, tmp_path, http_mode=True)
    err = _err(http.transcribe(path=str(src), opts=TranscribeOptions(), progress=None))
    assert err.code == "paths_not_allowed" and err.status == 403

    allowed = _backend(upload_dir, tmp_path, http_mode=True, allow_paths=True)
    assert _run(allowed.transcribe(path=str(src), opts=TranscribeOptions(), progress=None))["source"]["kind"] == "path"

    rooted = _backend(upload_dir, tmp_path, http_mode=True, allow_paths=True, path_root=tmp_path / "root")
    (tmp_path / "root").mkdir()
    err = _err(rooted.transcribe(path=str(src), opts=TranscribeOptions(), progress=None))
    assert err.code == "path_outside_root" and err.status == 403
    inside = _wav(tmp_path / "root", "in.wav")
    assert _run(rooted.transcribe(path=str(inside), opts=TranscribeOptions(), progress=None))["source"]["name"] == "in.wav"
    # symlink escaping the root is resolved before the check
    link = tmp_path / "root" / "link.wav"
    link.symlink_to(src)
    assert _err(rooted.transcribe(path=str(link), opts=TranscribeOptions(), progress=None)).code == "path_outside_root"

    local = _backend(upload_dir, tmp_path)
    err = _err(local.transcribe(path=str(tmp_path / "missing.wav"), opts=TranscribeOptions(), progress=None))
    assert err.code == "file_not_found" and err.status == 404
    assert _err(local.transcribe(path=str(tmp_path), opts=TranscribeOptions(), progress=None)).code == "file_not_found"
    (tmp_path / "notes.txt").write_text("x")
    assert _err(local.transcribe(path=str(tmp_path / "notes.txt"), opts=TranscribeOptions(), progress=None)).code == "unsupported_file"
    big = tmp_path / "big.wav"
    big.write_bytes(b"x" * 10_001)
    err = _err(local.transcribe(path=str(big), opts=TranscribeOptions(), progress=None))
    assert err.code == "file_too_large" and err.status == 413


def test_path_source_is_never_deleted(backend, tmp_path):
    src = _wav(tmp_path)
    _run(backend.transcribe(path=str(src), opts=TranscribeOptions(), progress=None))
    assert src.exists()


# ---------- options ----------


def test_model_and_option_validation(backend, tmp_path):
    src = _wav(tmp_path)

    def call(**kw):
        return _err(backend.transcribe(path=str(src), opts=TranscribeOptions(**kw), progress=None))

    assert call(model="nope").code == "model_not_found"
    assert call(model="nope").status == 404
    assert call(format="xml").code == "invalid_request"
    err = call(diarize=True, diarization_backend="sortformer", num_speakers=2)
    assert err.code == "unsupported_parameter" and err.param == "num_speakers"
    assert call(diarization_backend="magic").param == "diarization_backend"
    for bad in (0, -1, True, 1.5, "2"):
        err = call(num_speakers=bad)
        assert err.code == "unsupported_parameter" and err.param == "num_speakers" and err.status == 400
    assert call(asr_backend="quantum").param == "asr_backend"
    assert call(audio_preprocessing="loud").code == "unsupported_parameter"


def test_model_alias_is_accepted(backend, tmp_path):
    src = _wav(tmp_path)
    result = _run(backend.transcribe(path=str(src), opts=TranscribeOptions(model="whisper-1"), progress=None))
    assert result["text"]


def test_diarization_requires_hf_token_for_pyannote(upload_dir, tmp_path, fake_processor):
    src = _wav(tmp_path)
    no_token = _backend(upload_dir, tmp_path, hf_token="")
    err = _err(no_token.transcribe(path=str(src), opts=TranscribeOptions(format="diarized"), progress=None))
    assert err.code == "diarization_unavailable" and err.status == 503
    # sortformer does not need the token
    ok = _run(no_token.transcribe(path=str(src), opts=TranscribeOptions(diarize=True, diarization_backend="sortformer"),
                                  progress=None))
    assert fake_processor.calls[-1]["enable_diarization"] is True
    assert fake_processor.calls[-1]["diarization_backend"] == "sortformer"
    assert ok["text"]


def test_diarized_format_enables_diarization_and_speakers(backend, tmp_path, fake_processor):
    src = _wav(tmp_path)
    result = _run(backend.transcribe(path=str(src), opts=TranscribeOptions(format="diarized"), progress=None))
    assert fake_processor.calls[0]["enable_diarization"] is True
    assert [s["speaker"] for s in result["segments"]] == ["A", "B"]


def test_model_not_loaded(upload_dir, tmp_path, fake_processor):
    src = _wav(tmp_path)
    err = _err(_backend(upload_dir, tmp_path, model_loader=None).transcribe(
        path=str(src), opts=TranscribeOptions(), progress=None))
    assert err.code == "model_not_loaded" and err.status == 503


def test_processor_options_are_forwarded(backend, tmp_path, fake_processor):
    src = _wav(tmp_path)
    _run(backend.transcribe(path=str(src), opts=TranscribeOptions(audio_preprocessing="off", num_speakers=3, diarize=True),
                            progress=None))
    call = fake_processor.calls[0]
    assert call["audio_preprocessing_mode"] == "off" and call["num_speakers"] == 3
    assert call["diarization_backend"] == "pyannote"


def test_default_audio_preprocessing_comes_from_config(backend, tmp_path, fake_processor, monkeypatch):
    monkeypatch.setattr(mcp_backend, "AUDIO_PREPROCESSING_MODE", "denoise")
    _run(backend.transcribe(path=str(_wav(tmp_path)), opts=TranscribeOptions(), progress=None))
    assert fake_processor.calls[0]["audio_preprocessing_mode"] == "denoise"


# ---------- failures and cleanup ----------


def test_processor_failure_cleans_up_and_maps_to_processing_failed(backend, upload_dir, fake_processor):
    fake_processor.fail = True
    payload = base64.b64encode(b"ID3").decode()
    err = _err(backend.transcribe(audio_base64=payload, filename="a.mp3", opts=TranscribeOptions(), progress=None))
    assert err.code == "processing_failed" and err.status == 500
    assert _leftovers(upload_dir) == []


def test_processor_exception_cleans_up(backend, upload_dir, fake_processor, monkeypatch):
    monkeypatch.setattr(_FakeProcessor, "process_file", lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    err = _err(backend.transcribe(url="https://example.org/x", opts=TranscribeOptions(), progress=None))
    assert err.code == "processing_failed"
    assert _leftovers(upload_dir) == []


def test_per_request_loader_is_unloaded(backend, tmp_path, fake_processor, monkeypatch):
    src = _wav(tmp_path)
    _run(backend.transcribe(path=str(src), opts=TranscribeOptions(asr_backend="pytorch"), progress=None))
    assert len(_OwnedLoader.instances) == 1
    assert _OwnedLoader.instances[0].kw["requested_backend"] == "pytorch"
    assert _OwnedLoader.instances[0].unloaded == 1

    monkeypatch.setattr(_FakeProcessor, "process_file", lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    _err(backend.transcribe(path=str(src), opts=TranscribeOptions(asr_backend="pytorch"), progress=None))
    assert len(_OwnedLoader.instances) == 2 and _OwnedLoader.instances[1].unloaded == 1


def test_default_loader_is_never_unloaded(backend, tmp_path, fake_processor):
    calls = []
    backend.model_loader.unload = lambda: calls.append(1)
    _run(backend.transcribe(path=str(_wav(tmp_path)), opts=TranscribeOptions(), progress=None))
    assert _OwnedLoader.instances == [] and calls == []


def test_owned_loader_load_failure(backend, tmp_path, fake_processor, monkeypatch):
    monkeypatch.setattr(_OwnedLoader, "load_model", lambda self, logger=None: False)
    err = _err(backend.transcribe(path=str(_wav(tmp_path)), opts=TranscribeOptions(asr_backend="pytorch"), progress=None))
    assert err.code == "processing_failed"
    assert _OwnedLoader.instances[0].unloaded == 1


def test_run_transcription_adapts_progress_event_objects(tmp_path, fake_processor, monkeypatch):
    class Event:
        stage = "conversion"
        file_progress = 0.25

    def process_file(self, *a, **kw):
        self.progress_callback(Event())
        self.progress_callback("diarization", None)
        return {"success": True, "media_duration": 1.0, "utterances": list(UTTS), "diarization": {}}

    monkeypatch.setattr(_FakeProcessor, "process_file", process_file)
    events = []
    work = tmp_path / "work"
    work.mkdir()
    loader = _FakeModelLoader()
    opts = prepare_options(TranscribeOptions(), loader, hf_token="hf_x")
    assert (opts.asr_backend, opts.onnx_provider, opts.model) == ("auto", "auto", "v3_e2e_rnnt")
    result = run_transcription(_wav(tmp_path), work, opts, model_loader=loader,
                               stats_manager=None, loader_factory=_OwnedLoader, logger=None,
                               progress=lambda s, v: events.append((s, v)))
    assert result["utterances"] == UTTS
    assert events == [("conversion", 0.25), ("diarization", None)]
    with pytest.raises(ValueError):  # сырые опции без prepare_options не принимаются
        run_transcription(_wav(tmp_path), work, TranscribeOptions(), model_loader=loader, stats_manager=None,
                          loader_factory=_OwnedLoader, logger=None, progress=None)


def test_transcribe_runs_under_the_semaphore(backend, tmp_path, fake_processor):
    src = _wav(tmp_path)

    async def scenario():
        async with backend.semaphore:
            task = asyncio.ensure_future(backend.transcribe(path=str(src), opts=TranscribeOptions(), progress=None))
            await asyncio.sleep(0.05)
            assert not task.done() and fake_processor.calls == []
            assert backend.status()["busy"] == {"active": 0, "max": 1}
        return await task

    assert _run(scenario())["text"]


def test_cancel_leaves_cleanup_and_permit_to_the_worker_thread(backend, upload_dir, tmp_path, fake_processor, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow(self, *a, **kw):
        started.set()
        release.wait(5)
        return {"success": True, "media_duration": 1.0, "utterances": list(UTTS), "diarization": {}}

    monkeypatch.setattr(_FakeProcessor, "process_file", slow)

    async def scenario():
        task = asyncio.ensure_future(backend.transcribe(path=str(_wav(tmp_path)), opts=TranscribeOptions(), progress=None))
        await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
        assert backend.status()["busy"] == {"active": 1, "max": 1} and backend.semaphore.locked()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Поток ещё пишет — директория и разрешение семафора остаются за ним
        await asyncio.sleep(0.05)
        assert len(_leftovers(upload_dir)) == 1 and backend.semaphore.locked()
        assert backend.status()["busy"] == {"active": 1, "max": 1}
        release.set()
        for _ in range(100):
            if not _leftovers(upload_dir) and not backend.semaphore.locked():
                break
            await asyncio.sleep(0.02)
        assert _leftovers(upload_dir) == [] and not backend.semaphore.locked()
        assert backend.status()["busy"] == {"active": 0, "max": 1}

    _run(scenario())


def test_cancel_while_waiting_for_the_semaphore_cleans_up_immediately(backend, upload_dir, tmp_path, fake_processor):
    async def scenario():
        async with backend.semaphore:
            task = asyncio.ensure_future(backend.transcribe(path=str(_wav(tmp_path)), opts=TranscribeOptions(), progress=None))
            await asyncio.sleep(0.05)
            assert len(_leftovers(upload_dir)) == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert _leftovers(upload_dir) == [] and fake_processor.calls == []
        assert not backend.semaphore.locked()

    _run(scenario())


# ---------- render_result ----------


RESULT = {"utterances": list(UTTS), "media_duration": 3.25, "diarization": {"applied": False}}


def test_render_result_json_and_text():
    for fmt in ("json", "text"):
        out = render_result(RESULT, TranscribeOptions(format=fmt))
        assert set(out) == {"text", "duration", "language", "usage"}
        assert out["text"] == "Привет, как дела?" and out["duration"] == 3.25 and out["language"] == "ru"


def test_render_result_verbose_segments_and_words():
    out = render_result(RESULT, TranscribeOptions(format="verbose", language="en"))
    assert set(out) == {"text", "duration", "language", "usage", "segments", "diarization"}
    assert out["language"] == "en" and [s["text"] for s in out["segments"]] == ["Привет,", "как дела?"]
    assert "speaker" not in out["segments"][0]
    assert out["diarization"] == {"requested": False, "applied": False}
    out = render_result(RESULT, TranscribeOptions(format="verbose", word_timestamps=True, diarize=True))
    assert [w["word"] for w in out["words"]] == ["как", "дела?"]
    assert out["segments"][0]["speaker"] == "A"
    assert out["diarization"] == {"requested": True, "applied": False}


def test_render_result_diarized():
    out = render_result({**RESULT, "diarization": {"requested": True, "applied": True}}, TranscribeOptions(format="diarized"))
    assert set(out) == {"text", "duration", "language", "usage", "segments", "diarization"}
    assert [(s["speaker"], s["start"], s["end"]) for s in out["segments"]] == [("A", 0.0, 1.5), ("B", 1.5, 3.25)]
    assert out["diarization"] == {"requested": True, "applied": True}
    # диаризация не применилась (нет токена/бэкенда) — спикеры-заглушки, и агент это видит
    out = render_result(RESULT, TranscribeOptions(format="diarized"))
    assert out["diarization"] == {"requested": False, "applied": False}


def test_render_result_subtitles():
    srt = render_result(RESULT, TranscribeOptions(format="srt"))
    vtt = render_result(RESULT, TranscribeOptions(format="vtt"))
    assert set(srt) == {"text", "duration", "language", "usage", "subtitles"}
    assert "-->" in srt["subtitles"] and srt["subtitles"].startswith("1\n")
    assert vtt["subtitles"].startswith("WEBVTT")


# ---------- summarize ----------


@pytest.fixture
def fake_provider(monkeypatch):
    calls = []

    def run_provider(settings, text, prompt, *, provider, strict_empty_cli, **_):
        calls.append({"settings": settings, "text": text, "prompt": prompt, "provider": provider,
                      "strict": strict_empty_cli})
        return f"answer from {provider}"

    monkeypatch.setattr(llm_service, "run_provider", run_provider)
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_URL", "LLM_API_KEY", "LLM_TEMPERATURE"):
        monkeypatch.delenv(name, raising=False)
    return calls


def test_summarize_modes(backend, fake_provider):
    out = _run(backend.summarize("текст", "summary", None, None, None))
    assert out == {"mode": "summary", "provider": "API", "model": "", "answer": "answer from API"}
    call = fake_provider[0]
    assert call["prompt"] == PROMPTS["summary"] and call["text"] == "текст" and call["strict"] is True
    out = _run(backend.summarize("текст", "tasks", None, None, None))
    assert fake_provider[1]["prompt"] == PROMPTS["tasks"] and out["mode"] == "tasks"
    out = _run(backend.summarize("текст", "custom", "Переведи", None, None))
    assert fake_provider[2]["prompt"] == "Переведи" and out["mode"] == "custom"


def test_summarize_custom_requires_prompt(backend, fake_provider):
    err = _err(backend.summarize("текст", "custom", None, None, None))
    assert err.code == "prompt_required" and fake_provider == []
    assert _err(backend.summarize("текст", "custom", "   ", None, None)).code == "prompt_required"


def test_summarize_rejects_unknown_mode_and_empty_text(backend, fake_provider):
    assert _err(backend.summarize("текст", "poetry", None, None, None)).code == "unsupported_parameter"
    assert _err(backend.summarize("   ", "summary", None, None, None)).code == "invalid_request"
    assert fake_provider == []


def test_summarize_provider_and_model_overrides(backend, fake_provider):
    out = _run(backend.summarize("t", "summary", None, "Другое", "gpt-x"))
    assert out["provider"] == "Other" and out["model"] == "gpt-x"
    call = fake_provider[0]
    assert call["provider"] == "Other" and call["settings"]["provider"] == "Other" and call["settings"]["model"] == "gpt-x"
    err = _err(backend.summarize("t", "summary", None, "Skynet", None))
    assert err.code == "unsupported_parameter" and err.param == "provider"


def test_summarize_provider_from_settings(backend, fake_provider, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "user_settings.json").write_text('{"llm_provider": "Claude Code", "llm_model": "sonnet"}')
    out = _run(backend.summarize("t", "summary", None, None, None))
    assert out["provider"] == "Claude Code" and out["model"] == "sonnet"
    assert fake_provider[0]["provider"] == "Claude Code"


def test_summarize_provider_failure(backend, fake_provider, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("claude exited with 1")
    monkeypatch.setattr(llm_service, "run_provider", boom)
    err = _err(backend.summarize("t", "summary", None, None, None))
    assert err.code == "llm_failed" and err.status == 502 and "claude exited" in err.message


# ---------- models / providers / status ----------


def test_models_payload_shape(backend):
    out = backend.models()
    assert out["object"] == "list" and {m["id"] for m in out["data"]} >= {"v3_e2e_rnnt"}
    default = [m for m in out["data"] if m["default"]]
    assert len(default) == 1 and "whisper-1" in default[0]["aliases"]
    assert set(out["gigaam"]) == {"backends", "onnx_providers", "active"}
    assert out["gigaam"]["active"]["active_backend"] == "mlx"


def test_resolve_model_and_aliases():
    assert mcp_backend.resolve_model(None) == mcp_backend.DEFAULT_MODEL
    assert mcp_backend.resolve_model("whisper-1") == mcp_backend.DEFAULT_MODEL
    with pytest.raises(BackendError) as info:
        mcp_backend.resolve_model("nope")
    assert info.value.code == "model_not_found" and info.value.status == 404 and info.value.param == "model"


def test_llm_providers(backend, monkeypatch, tmp_path):
    statuses = [
        cli_tools.ToolStatus("claude", "Claude Code", "found", "/usr/bin/claude", "1.2.3", None, ""),
        cli_tools.ToolStatus("codex", "Codex", "missing", None, None, None, "npm i"),
    ]
    monkeypatch.setattr(cli_tools, "scan", lambda *a, **kw: statuses)
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    out = backend.llm_providers()
    assert out["providers"] == [
        {"name": "Claude Code", "available": True, "version": "1.2.3", "path": "/usr/bin/claude"},
        {"name": "Codex", "available": False, "version": None, "path": None},
    ]
    assert out["api"] == {"configured": False, "model": ""}
    monkeypatch.setenv("LLM_API_KEY", "sk-1")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    assert backend.llm_providers()["api"] == {"configured": True, "model": "gpt-5"}


def test_status(backend, upload_dir, tmp_path):
    out = backend.status()
    assert set(out) == {"version", "runtime", "asr", "busy", "limits"}
    assert out["busy"] == {"active": 0, "max": 1} and out["asr"]["active_backend"] == "mlx"
    assert out["limits"] == {"max_file_mb": 10_000 / (1024 * 1024), "max_inline_mb": 1000 / (1024 * 1024),
                             "max_concurrent": 1}
    unlimited = _backend(upload_dir, tmp_path, semaphore=None, max_concurrent=None)
    assert unlimited.status()["busy"] == {"active": 0, "max": None}
    assert unlimited.status()["limits"]["max_concurrent"] is None


def test_backend_error_str_is_prefixed_with_code():
    err = BackendError("unsupported_file", "Unsupported file type", 400)
    assert str(err) == "[unsupported_file] Unsupported file type" and err.message == "Unsupported file type"
