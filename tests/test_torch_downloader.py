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
