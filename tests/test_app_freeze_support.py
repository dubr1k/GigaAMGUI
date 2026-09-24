"""``app.py`` must hand multiprocessing helpers to PyInstaller before doing anything.

PyInstaller's ``pyi_rth_multiprocessing`` runtime hook only takes effect through
``multiprocessing.freeze_support()``: it replaces that function with one that
recognises the ``resource_tracker``/``forkserver`` helper argv
(``-B -S -I -c "from multiprocessing.resource_tracker import main;main(fd)"``) and
runs the helper inline. Without the call, a frozen GUI executable re-enters
the Qt branch and may start a second application; the separate Liquid worker
also needs its own freeze_support() before importing the processing pipeline.
"""

import runpy
import sys
import types

import pytest

APP_PATH = "app.py"
HELPER_ARGV = ["app.py", "-B", "-S", "-I", "-c", "from multiprocessing.resource_tracker import main;main(7)"]


@pytest.fixture
def freeze_support_calls(monkeypatch):
    import multiprocessing

    calls = []
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: calls.append("freeze_support"))
    return calls


def test_freeze_support_runs_before_the_native_worker(monkeypatch, freeze_support_calls):
    calls = freeze_support_calls
    monkeypatch.setitem(
        sys.modules,
        "src.tui_worker",
        types.SimpleNamespace(main=lambda: calls.append("worker") or 0),
    )
    monkeypatch.setattr(sys, "argv", ["app.py", "--native-worker"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(APP_PATH, run_name="__main__")

    assert exit_info.value.code == 0
    assert calls == ["freeze_support", "worker"]


def test_freeze_support_runs_before_selfcheck(monkeypatch, freeze_support_calls):
    calls = freeze_support_calls
    monkeypatch.setitem(
        sys.modules,
        "src.selfcheck",
        types.SimpleNamespace(run_selfcheck=lambda: calls.append("selfcheck") or 0),
    )
    monkeypatch.setattr(sys, "argv", ["app.py", "--selfcheck"])

    with pytest.raises(SystemExit):
        runpy.run_path(APP_PATH, run_name="__main__")

    assert calls == ["freeze_support", "selfcheck"]


def test_helper_argv_is_diverted_before_the_gui(monkeypatch):
    """With the PyInstaller hook in place the helper argv never reaches ``main()``."""
    import multiprocessing

    def divert():
        if "-c" in sys.argv:
            raise SystemExit(0)

    monkeypatch.setattr(multiprocessing, "freeze_support", divert)
    monkeypatch.setattr(sys, "argv", HELPER_ARGV)
    monkeypatch.setitem(
        sys.modules,
        "src.selfcheck",
        types.SimpleNamespace(run_selfcheck=lambda: pytest.fail("helper argv reached --selfcheck")),
    )

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(APP_PATH, run_name="__main__")

    assert exit_info.value.code == 0
