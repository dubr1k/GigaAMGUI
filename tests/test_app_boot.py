import app


def test_boot_does_not_require_torch_when_mlx_is_available(monkeypatch):
    monkeypatch.setattr(app, "ASR_BACKEND", "auto")
    monkeypatch.setattr(app, "_is_mlx_available", lambda: True)
    monkeypatch.setattr(app.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(app.platform, "machine", lambda: "arm64")

    assert app._boot_requires_torch() is False


def test_boot_requires_torch_when_auto_and_mlx_is_unavailable(monkeypatch):
    monkeypatch.setattr(app, "ASR_BACKEND", "auto")
    monkeypatch.setattr(app, "_is_mlx_available", lambda: False)
    monkeypatch.setattr(app.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(app.platform, "machine", lambda: "arm64")

    assert app._boot_requires_torch() is True


def test_boot_does_not_require_torch_for_explicit_onnx(monkeypatch):
    monkeypatch.setattr(app, "ASR_BACKEND", "onnx")

    assert app._boot_requires_torch() is False


def test_boot_still_requires_torch_for_non_macos_auto_before_quality_gate(monkeypatch):
    monkeypatch.setattr(app, "ASR_BACKEND", "auto")
    monkeypatch.setattr(app, "_is_onnx_available", lambda: True)
    monkeypatch.setattr(app.sys, "platform", "linux", raising=False)

    assert app._boot_requires_torch() is True
