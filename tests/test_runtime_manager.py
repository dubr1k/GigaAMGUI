import os
import sys
import types

from src.utils import runtime_manager as rm

_TEST_STACK = {
    "torch": "2.6.0",
    "torchaudio": "2.6.0",
    "torchvision": "0.21.0",
}

_TEST_VARIANTS = {
    "cpu": {
        "label": "CPU",
        "index": "https://example.invalid/cpu",
        "packages": _TEST_STACK,
        "torch_device": "cpu",
        "size_hint": "~1 MB",
        "hint": "cpu",
    },
    "cu124": {
        "label": "CUDA",
        "index": "https://example.invalid/cu124",
        "packages": _TEST_STACK,
        "torch_device": "cuda",
        "size_hint": "~1 MB",
        "hint": "cuda",
    },
}


def _write_stack(path, versions=None) -> None:
    versions = versions or rm.VARIANTS[path.name]["packages"]
    for package, version in versions.items():
        package_dir = path / package / "__init__.py"
        package_dir.parent.mkdir(parents=True, exist_ok=True)
        package_dir.write_text("", encoding="utf-8")
        (path / f"{package}-{version}.dist-info").mkdir(parents=True, exist_ok=True)


def _mark_installed(variant: str) -> None:
    path = rm.variant_dir(variant)
    _write_stack(path)
    (path / ".installed_ok").write_text("ok", encoding="utf-8")


def test_switch_runtime_purges_old_modules_and_activates_new(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(rm, "VARIANTS", dict(_TEST_VARIANTS))

    _mark_installed("cpu")
    _mark_installed("cu124")

    rm.activate("cpu")
    old_runtime_root = rm.variant_dir("cpu")
    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(__file__=str(old_runtime_root / "torch" / "__init__.py")),
    )
    monkeypatch.setitem(
        sys.modules,
        "gigaam",
        types.SimpleNamespace(__file__=str(old_runtime_root / "gigaam" / "__init__.py")),
    )
    monkeypatch.setitem(sys.modules, "unrelated_module", types.SimpleNamespace(__file__=__file__))

    assert rm.switch_runtime("cu124") is True

    assert os.environ["GIGAAM_ACTIVE_VARIANT"] == "cu124"
    assert sys.path[0] == str(rm.variant_dir("cu124"))
    assert rm.get_selected_variant() == "cu124"
    assert "torch" not in sys.modules
    assert "gigaam" not in sys.modules
    assert "unrelated_module" in sys.modules


def test_purge_runtime_modules_removes_prefixed_modules(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(rm, "VARIANTS", dict(_TEST_VARIANTS))
    _mark_installed("cpu")

    monkeypatch.setitem(sys.modules, "pyannote.audio", types.SimpleNamespace(__file__=__file__))
    monkeypatch.setitem(sys.modules, "torchaudio", types.SimpleNamespace(__file__=__file__))

    removed = rm.purge_runtime_modules()

    assert removed >= 2
    assert "pyannote.audio" not in sys.modules
    assert "torchaudio" not in sys.modules


def test_is_installed_rejects_mixed_runtime_versions(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(rm, "VARIANTS", dict(_TEST_VARIANTS))
    path = rm.variant_dir("cpu")
    mixed = dict(_TEST_STACK, torchaudio="2.11.0")
    _write_stack(path, mixed)
    (path / ".installed_ok").write_text("ok", encoding="utf-8")

    assert rm.is_installed("cpu") is False


def test_install_variant_replaces_stale_runtime_transactionally(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(rm, "VARIANTS", dict(_TEST_VARIANTS))
    target = rm.variant_dir("cpu")
    target.mkdir(parents=True)
    stale_file = target / "stale.txt"
    stale_file.write_text("old runtime", encoding="utf-8")
    (target / ".installed_ok").write_text("ok", encoding="utf-8")
    calls = []

    def fake_install(*, target, versions, **kwargs):
        calls.append((target, versions, kwargs))
        _write_stack(target, versions)

    monkeypatch.setattr(rm.torch_downloader, "install", fake_install)

    assert rm.install_variant("cpu") is True
    assert rm.is_installed("cpu") is True
    assert not stale_file.exists()
    assert calls[0][1] == _TEST_STACK
    assert calls[0][0].name == ".cpu.installing"


def test_cancelled_install_keeps_previous_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(rm, "VARIANTS", dict(_TEST_VARIANTS))
    target = rm.variant_dir("cpu")
    target.mkdir(parents=True)
    stale_file = target / "stale.txt"
    stale_file.write_text("keep me", encoding="utf-8")

    def cancel_install(**_kwargs):
        raise rm.torch_downloader.DownloadCancelled("cancelled")

    monkeypatch.setattr(rm.torch_downloader, "install", cancel_install)

    assert rm.install_variant("cpu") is False
    assert stale_file.read_text(encoding="utf-8") == "keep me"
