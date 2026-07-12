import sys

from src import selfcheck


def test_selfcheck_reports_module_that_fails(monkeypatch, capsys):
    # Заставляем один из проверяемых импортов упасть — selfcheck обязан вернуть 1
    # и напечатать имя модуля.
    def fake_import(name):
        if name == "pyannote.audio":
            raise ImportError("cannot import name 'ImageEnhance' from 'PIL'")
    monkeypatch.setattr(selfcheck, "_import_module", fake_import)

    rc = selfcheck.run_selfcheck(check_torch=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert "pyannote.audio" in out
    assert "ImageEnhance" in out


def test_selfcheck_passes_when_all_import(monkeypatch):
    monkeypatch.setattr(selfcheck, "_import_module", lambda name: None)
    assert selfcheck.run_selfcheck(check_torch=False) == 0


def test_selfcheck_applies_app_patches_before_chain(monkeypatch):
    # check_torch=True: сперва настройка torch, затем те же патчи, что и в
    # приложении (заглушка torchaudio.set_audio_backend), и только потом импорт
    # цепочки. Порядок критичен: pyannote.audio импортируется после патча.
    calls = []
    monkeypatch.setattr(selfcheck, "_ensure_torch", lambda: calls.append("torch"))
    monkeypatch.setattr(selfcheck, "_apply_app_patches", lambda: calls.append("patch"))
    monkeypatch.setattr(selfcheck, "_import_module", lambda name: calls.append(f"import:{name}"))

    rc = selfcheck.run_selfcheck(check_torch=True)
    assert rc == 0
    assert calls[0] == "torch"
    assert calls[1] == "patch"
    assert calls[2].startswith("import:")  # цепочка импортируется только после патча


def test_selfcheck_reports_patch_failure(monkeypatch):
    # Падение применения патчей — отдельный диагностируемый провал, не «not installed».
    monkeypatch.setattr(selfcheck, "_ensure_torch", lambda: None)

    def boom():
        raise RuntimeError("patch boom")
    monkeypatch.setattr(selfcheck, "_apply_app_patches", boom)
    monkeypatch.setattr(selfcheck, "_import_module", lambda name: None)

    assert selfcheck.run_selfcheck(check_torch=True) == 1


def test_selfcheck_survives_none_streams(monkeypatch, tmp_path):
    # На windowed-сборке (console=False) sys.stdout/stderr могут быть None.
    # selfcheck не должен падать и обязан вернуть корректный код.
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(selfcheck, "_import_module", lambda name: None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert selfcheck.run_selfcheck(check_torch=False) == 0
    # Диагностика должна была записаться в файл, несмотря на None-потоки.
    assert (tmp_path / "selfcheck.log").exists()
