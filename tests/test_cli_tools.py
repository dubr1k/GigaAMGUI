"""Реестр LLM-провайдеров и резолвер CLI-инструментов (src/services/cli_tools)."""
from __future__ import annotations

import os
import subprocess

import pytest

from src.services import cli_tools


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _fresh_cache():
    cli_tools.invalidate_cache()
    yield
    cli_tools.invalidate_cache()


# --- реестр -----------------------------------------------------------------

def test_registry_order_and_names():
    names = cli_tools.canonical_provider_names()
    assert names == ["API", "Claude Code", "Codex", "OpenCode", "Pi", "oh-my-pi", "Other"]


def test_omp_spec():
    spec = cli_tools.provider_by_name("oh-my-pi")
    assert spec.id == "omp"
    assert spec.binary == "omp"
    assert spec.settings_prefix == "omp"
    assert spec.has_provider_field is True
    assert "omp" in spec.install_hint


def test_provider_by_name_accepts_russian_other_alias():
    assert cli_tools.provider_by_name("Другое").id == "other"
    assert cli_tools.provider_by_name("Other").id == "other"


def test_provider_by_name_unknown_raises():
    with pytest.raises(KeyError):
        cli_tools.provider_by_name("Nope")


def test_cli_specs_exclude_api_and_other():
    ids = [spec.id for spec in cli_tools.cli_specs()]
    assert ids == ["claude", "codex", "opencode", "pi", "omp"]


# --- search_dirs ------------------------------------------------------------

def test_search_dirs_prepends_path_and_appends_existing_known_dirs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".bun" / "bin").mkdir(parents=True)
    (home / ".local" / "bin").mkdir(parents=True)
    nvm_old = home / ".nvm" / "versions" / "node" / "v18.0.0" / "bin"
    nvm_new = home / ".nvm" / "versions" / "node" / "v22.1.0" / "bin"
    nvm_old.mkdir(parents=True)
    nvm_new.mkdir(parents=True)
    path_dir = tmp_path / "onpath"
    path_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setattr(cli_tools.sys, "platform", "darwin")
    monkeypatch.setattr(cli_tools.os, "name", "posix")

    dirs = cli_tools.search_dirs()

    assert dirs[0] == str(path_dir)
    assert str(home / ".bun" / "bin") in dirs
    assert str(home / ".local" / "bin") in dirs
    # nvm: новые версии раньше старых
    assert dirs.index(str(nvm_new)) < dirs.index(str(nvm_old))
    # несуществующие каталоги отфильтрованы
    assert not any(d.endswith(".cargo/bin") for d in dirs)
    assert len(dirs) == len(set(dirs))


def test_search_dirs_windows_locations(tmp_path, monkeypatch):
    appdata = tmp_path / "AppData" / "Roaming"
    (appdata / "npm").mkdir(parents=True)
    profile = tmp_path
    (profile / ".bun" / "bin").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(cli_tools.os, "name", "nt")
    monkeypatch.setattr(cli_tools.sys, "platform", "win32")

    dirs = cli_tools.search_dirs()

    assert str(appdata / "npm") in dirs
    assert str(profile / ".bun" / "bin") in dirs


def test_child_environment_sets_extended_path(monkeypatch):
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: ["/a", "/b"])
    env = cli_tools.child_environment({"FOO": "1", "PATH": "/x"})
    assert env["FOO"] == "1"
    assert env["PATH"] == os.pathsep.join(["/a", "/b"])


# --- locate / resolve -------------------------------------------------------

def _make_exe(directory, name):
    path = directory / name
    path.write_text("#!/bin/sh\necho 1.2.3\n")
    path.chmod(0o755)
    return path


def test_locate_tool_finds_binary_in_known_dir(tmp_path, monkeypatch):
    exe = _make_exe(tmp_path, "omp")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    spec = cli_tools.provider_by_name("oh-my-pi")
    assert cli_tools.locate_tool(spec) == str(exe)


def test_locate_tool_override_bare_name_is_searched(tmp_path, monkeypatch):
    exe = _make_exe(tmp_path, "my-omp")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    spec = cli_tools.provider_by_name("oh-my-pi")
    assert cli_tools.locate_tool(spec, "my-omp") == str(exe)


def test_locate_tool_override_path_is_used_verbatim(tmp_path, monkeypatch):
    exe = _make_exe(tmp_path, "omp")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [])
    spec = cli_tools.provider_by_name("oh-my-pi")
    assert cli_tools.locate_tool(spec, str(exe)) == str(exe)


def test_locate_tool_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    spec = cli_tools.provider_by_name("oh-my-pi")
    assert cli_tools.locate_tool(spec) is None
    assert cli_tools.locate_tool(spec, str(tmp_path / "nope")) is None


def test_resolve_tool_found_with_version(tmp_path, monkeypatch):
    exe = _make_exe(tmp_path, "omp")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "omp/18.2.5\nomp v18.2.5\n", ""))
    status = cli_tools.resolve_tool(cli_tools.provider_by_name("oh-my-pi"))
    assert status.status == "found"
    assert status.path == str(exe)
    assert status.version == "18.2.5"
    assert status.provider == "oh-my-pi"


def test_resolve_tool_version_from_stderr_and_prefixes(tmp_path, monkeypatch):
    _make_exe(tmp_path, "codex")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "", "codex-cli 0.154.0"))
    status = cli_tools.resolve_tool(cli_tools.provider_by_name("Codex"))
    assert status.version == "0.154.0"


def test_resolve_tool_missing_has_install_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    status = cli_tools.resolve_tool(cli_tools.provider_by_name("Pi"))
    assert status.status == "missing"
    assert status.path is None
    assert status.install_hint


def test_resolve_tool_broken_when_version_probe_fails(tmp_path, monkeypatch):
    exe = _make_exe(tmp_path, "opencode")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])

    def boom(*a, **k):
        raise OSError(193, "not a valid Win32 application")

    monkeypatch.setattr(subprocess, "run", boom)
    status = cli_tools.resolve_tool(cli_tools.provider_by_name("OpenCode"))
    assert status.status == "broken"
    assert status.path == str(exe)
    assert "Win32" in (status.detail or "")


def test_resolve_tool_broken_on_nonzero_exit(tmp_path, monkeypatch):
    _make_exe(tmp_path, "claude")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, "", "node: not found"))
    status = cli_tools.resolve_tool(cli_tools.provider_by_name("Claude Code"))
    assert status.status == "broken"
    assert "node: not found" in status.detail


def test_resolve_tool_broken_on_timeout(tmp_path, monkeypatch):
    _make_exe(tmp_path, "claude")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("claude", 10)

    monkeypatch.setattr(subprocess, "run", slow)
    status = cli_tools.resolve_tool(cli_tools.provider_by_name("Claude Code"))
    assert status.status == "broken"


def test_resolve_tool_probe_uses_child_environment(tmp_path, monkeypatch):
    _make_exe(tmp_path, "claude")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path), "/extra"])
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        captured["command"] = command
        return _Proc(0, "2.1.275 (Claude Code)", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cli_tools.resolve_tool(cli_tools.provider_by_name("Claude Code"))
    assert captured["command"][1:] == ["--version"]
    assert "/extra" in captured["env"]["PATH"]
    assert captured["timeout"] == cli_tools.PROBE_TIMEOUT


def test_resolve_api_and_other_not_applicable():
    assert cli_tools.resolve_tool(cli_tools.provider_by_name("API")).status == "not_applicable"
    assert cli_tools.resolve_tool(cli_tools.provider_by_name("Other")).status == "not_applicable"


def test_to_dict_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    data = cli_tools.resolve_tool(cli_tools.provider_by_name("Pi")).to_dict()
    assert set(data) == {"id", "provider", "status", "path", "version", "detail", "install_hint"}


# --- scan -------------------------------------------------------------------

def test_scan_covers_all_cli_specs_and_caches(tmp_path, monkeypatch):
    _make_exe(tmp_path, "omp")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        return _Proc(0, "1.0.0", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    first = cli_tools.scan()
    second = cli_tools.scan()

    assert [s.id for s in first] == ["claude", "codex", "opencode", "pi", "omp"]
    assert first is second  # кэш
    assert calls == [str(tmp_path / "omp")]  # probe только для найденного

    third = cli_tools.scan(fresh=True)
    assert third is not first
    assert len(calls) == 2


def test_scan_overrides_are_part_of_cache_key(tmp_path, monkeypatch):
    exe = _make_exe(tmp_path, "custom-pi")
    monkeypatch.setattr(cli_tools, "search_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "0.85.1", ""))

    default = cli_tools.scan()
    overridden = cli_tools.scan({"pi": "custom-pi"})

    assert default is not overridden
    pi = next(s for s in overridden if s.id == "pi")
    assert pi.status == "found" and pi.path == str(exe)
