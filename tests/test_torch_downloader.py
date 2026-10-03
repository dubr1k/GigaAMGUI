from src.utils import torch_downloader


def test_install_requests_one_explicit_compatible_stack(monkeypatch, tmp_path):
    versions = {
        "torch": "2.8.0",
        "torchaudio": "2.8.0",
        "torchvision": "0.23.0",
    }
    requested = []
    extracted = []

    def fake_find_wheel(base, package, version=None, **_kwargs):
        requested.append((base, package, version))
        return f"https://example.invalid/{package}.whl", None, version

    def fake_extract(url, _sha, target, **kwargs):
        extracted.append((url, target, kwargs["name"]))

    monkeypatch.setattr(torch_downloader, "find_wheel", fake_find_wheel)
    monkeypatch.setattr(torch_downloader, "_download_and_extract", fake_extract)

    torch_downloader.install(
        "https://example.invalid/cu128",
        tmp_path,
        versions=versions,
    )

    assert requested == [
        ("https://example.invalid/cu128", "torch", "2.8.0"),
        ("https://example.invalid/cu128", "torchaudio", "2.8.0"),
        ("https://example.invalid/cu128", "torchvision", "0.23.0"),
    ]
    assert [name for _url, _target, name in extracted] == [
        "torch 2.8.0",
        "torchaudio 2.8.0",
        "torchvision 0.23.0",
    ]


def test_install_rejects_incomplete_version_map(tmp_path):
    try:
        torch_downloader.install(
            "https://example.invalid/cpu",
            tmp_path,
            versions={"torch": "2.6.0"},
        )
    except ValueError as exc:
        assert "torchaudio" in str(exc)
        assert "torchvision" in str(exc)
    else:
        raise AssertionError("incomplete runtime stack must be rejected")


def _nvidia_install(monkeypatch, tmp_path, base_index_error):
    requested = []
    extracted = []

    def fake_find_wheel(base, package, version=None, **_kwargs):
        requested.append((base, package, version))
        if package.startswith("nvidia") and "pypi.org" not in base:
            raise base_index_error
        return f"{base}/{package}.whl", None, version or "latest"

    monkeypatch.setattr(torch_downloader, "find_wheel", fake_find_wheel)
    monkeypatch.setattr(
        torch_downloader,
        "_download_and_extract",
        lambda url, _sha, target, **kwargs: extracted.append(kwargs["name"]),
    )
    monkeypatch.setattr(
        torch_downloader,
        "_nvidia_requirements",
        lambda _target: [("nvidia-cudnn-cu12", "9.1.0.70")],
    )
    torch_downloader.install(
        "https://example.invalid/whl/cu124",
        tmp_path,
        versions={"torch": "2.6.0", "torchaudio": "2.6.0", "torchvision": "0.21.0"},
        need_nvidia=True,
    )
    return requested, extracted


def test_nvidia_fallback_to_pypi_keeps_the_pinned_version(monkeypatch, tmp_path):
    # Прежний fallback ставил с PyPI последнюю версию nvidia-пакета, а не ту,
    # что требует torch: CUDA-библиотеки расходились с собранным torch.
    requested, extracted = _nvidia_install(monkeypatch, tmp_path, RuntimeError("no wheel"))

    assert ("https://pypi.org/simple", "nvidia-cudnn-cu12", "9.1.0.70") in requested
    assert "nvidia-cudnn-cu12 9.1.0.70" in extracted


def test_nvidia_fallback_also_covers_http_errors(monkeypatch, tmp_path):
    # 404 страницы пакета на индексе pytorch — urllib HTTPError, а не
    # RuntimeError: раньше он обрывал установку вместо перехода на PyPI.
    import urllib.error

    error = urllib.error.HTTPError("https://example.invalid/nvidia/", 404, "Not Found", None, None)
    _requested, extracted = _nvidia_install(monkeypatch, tmp_path, error)

    assert "nvidia-cudnn-cu12 9.1.0.70" in extracted


def test_nvidia_fallback_does_not_swallow_cancellation(monkeypatch, tmp_path):
    import pytest

    with pytest.raises(torch_downloader.DownloadCancelled):
        _nvidia_install(monkeypatch, tmp_path, torch_downloader.DownloadCancelled("cancel"))
