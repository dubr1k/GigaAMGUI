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
