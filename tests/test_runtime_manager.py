import os
import sys
import types

from src.utils import runtime_manager as rm


_TEST_VARIANTS = {
    "cpu": {"label": "CPU", "index": "https://example.invalid/cpu", "torch_device": "cpu", "size_hint": "~1 MB", "hint": "cpu"},
    "cu124": {"label": "CUDA", "index": "https://example.invalid/cu124", "torch_device": "cuda", "size_hint": "~1 MB", "hint": "cuda"},
}


def _mark_installed(variant: str) -> None:
    path = rm.variant_dir(variant)
    (path / "torch").mkdir(parents=True, exist_ok=True)
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
