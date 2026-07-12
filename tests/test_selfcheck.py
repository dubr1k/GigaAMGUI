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
