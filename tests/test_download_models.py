import download_models


def test_download_onnx_models_uses_public_names_and_runtime_options(tmp_path, monkeypatch):
    from src.utils import model_cache

    monkeypatch.setattr(model_cache, "resolve_bundled_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(model_cache, "hf_repo_is_cached", lambda *_a, **_k: False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    calls = []

    class Model:
        pass

    downloaded, failed = download_models.download_onnx_models(
        ["v3_e2e_rnnt", "multilingual_ctc"],
        provider="cpu",
        model_dir=str(tmp_path),
        loader=lambda *args, **kwargs: calls.append((args, kwargs)) or Model(),
    )

    assert downloaded == ["v3_e2e_rnnt", "multilingual_ctc"]
    assert failed == []
    # Корень ONNX_MODEL_DIR: у каждой модели свой подкаталог, который
    # onnx-asr дозагружает (offline=False), а не пустой общий каталог.
    assert calls[0] == (("gigaam-v3-e2e-rnnt",), {
        "path": tmp_path / "istupakov--gigaam-v3-onnx",
        "quantization": None,
        "providers": ["CPUExecutionProvider"],
        "preprocessor_config": {"use_numpy_preprocessors": False},
        "offline": False,
    })
    assert calls[1][1]["path"] == tmp_path / "istupakov--gigaam-multilingual-ctc-onnx"


def test_download_onnx_models_reports_individual_failure():
    downloaded, failed = download_models.download_onnx_models(
        ["v3_e2e_rnnt"],
        loader=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network")),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    assert downloaded == []
    assert failed == ["v3_e2e_rnnt"]


def test_download_pytorch_models_uses_selected_data_directory(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("GIGAAM_PYTORCH_MODEL_DIR", str(tmp_path))

    downloaded, failed = download_models.download_pytorch_models(
        loader=lambda model, **kwargs: calls.append((model, kwargs)) or object()
    )

    assert failed == []
    assert downloaded == ["v3_e2e_rnnt", "v3_e2e_ctc", "v3_ctc", "v3_rnnt"]
    assert all(kwargs["download_root"] == str(tmp_path) for _, kwargs in calls)
