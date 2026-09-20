"""Лаунчер ~/.local/bin/gigaam: --update/--version/--help и запуск бинаря."""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path("scripts/tui/gigaam-launcher.sh").resolve()
INSTALLER = Path("scripts/install_tui.sh").resolve()


def _fake_install(tmp_path: Path) -> Path:
    prefix = tmp_path / "gigaam-tui"
    repo = prefix / "repo"
    binary = repo / "tui" / "target" / "release" / "gigaam-tui"
    python = repo / ".venv" / "bin" / "python"
    for path, body in (
        (binary, '#!/usr/bin/env bash\necho "tui:$*"; echo "root=$GIGAAM_PROJECT_ROOT"; echo "python=$GIGAAM_PYTHON"\n'),
        (python, "#!/usr/bin/env bash\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return prefix


def _run(prefix: Path, *args: str, env: dict | None = None):
    full_env = {**os.environ, "GIGAAM_TUI_PREFIX": str(prefix), **(env or {})}
    return subprocess.run(["bash", str(LAUNCHER), *args], capture_output=True, text=True, env=full_env, timeout=30)


def test_launcher_execs_the_binary_with_project_env(tmp_path):
    prefix = _fake_install(tmp_path)
    result = _run(prefix, "--data-dir", "/x")
    assert result.returncode == 0, result.stderr
    assert "tui:--data-dir /x" in result.stdout
    assert f"root={prefix / 'repo'}" in result.stdout
    assert f"python={prefix / 'repo' / '.venv' / 'bin' / 'python'}" in result.stdout


def test_launcher_version_prints_the_installed_commit(tmp_path):
    prefix = _fake_install(tmp_path)
    head = subprocess.run(["git", "-C", str(prefix / "repo"), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    result = _run(prefix, "--version")
    assert result.returncode == 0
    assert head in result.stdout and str(prefix / "repo") in result.stdout


def test_launcher_update_runs_the_installer_with_the_same_prefix(tmp_path):
    prefix = _fake_install(tmp_path)
    installer = tmp_path / "fake_install.sh"
    installer.write_text('#!/usr/bin/env bash\necho "installer:$*"\n')
    result = _run(prefix, "--update", "--ref", "v2.2.1", env={"GIGAAM_INSTALLER": str(installer)})
    assert result.returncode == 0, result.stderr
    assert f"installer:--prefix {prefix} --ref v2.2.1" in result.stdout


def test_launcher_refuses_to_run_without_prefix(tmp_path):
    result = subprocess.run(["bash", str(LAUNCHER)], capture_output=True, text=True, env={**os.environ, "GIGAAM_TUI_PREFIX": ""})
    assert result.returncode == 2
    assert "GIGAAM_TUI_PREFIX" in result.stderr


def test_launcher_help_works_without_prefix():
    result = subprocess.run(
        ["bash", str(LAUNCHER), "--help"],
        capture_output=True,
        text=True,
        env={**os.environ, "GIGAAM_TUI_PREFIX": ""},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout


def test_launcher_update_via_download_failure_cleans_up_the_temp_file(tmp_path):
    prefix = _fake_install(tmp_path)
    local_installer = prefix / "repo" / "scripts" / "install_tui.sh"
    local_installer.parent.mkdir(parents=True, exist_ok=True)
    local_installer.write_text('#!/usr/bin/env bash\necho "installer:$*"\n')
    result = _run(
        prefix,
        "--update",
        env={
            "TMPDIR": str(tmp_path),
            "GIGAAM_REPOSITORY_RAW": "http://127.0.0.1:9",
        },
    )
    assert result.returncode == 0, result.stderr
    assert f"installer:--prefix {prefix}" in result.stdout
    assert list(tmp_path.glob("install_tui.*")) == []


def _ensure_path(tmp_path: Path, shell: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    (home / ".config" / "fish").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home), "SHELL": shell, "GIGAAM_INSTALL_STAGE": "path-only", "PATH": "/usr/bin:/bin"}
    return subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True, env=env, timeout=30), home


def test_installer_adds_local_bin_to_fish_config_once(tmp_path):
    result, home = _ensure_path(tmp_path, "/opt/homebrew/bin/fish")
    assert result.returncode == 0, result.stderr
    conf = home / ".config" / "fish" / "conf.d" / "gigaam.fish"
    assert "fish_add_path" in conf.read_text() and ".local/bin" in conf.read_text()
    result, _ = _ensure_path(tmp_path, "/opt/homebrew/bin/fish")
    assert conf.read_text().count("fish_add_path") == 1


def test_installer_adds_local_bin_to_zsh_and_bash_rc_once(tmp_path):
    for shell, rc in (("/bin/zsh", ".zshrc"), ("/bin/bash", ".bashrc")):
        result, home = _ensure_path(tmp_path, shell)
        assert result.returncode == 0, result.stderr
        text = (home / rc).read_text()
        assert '# gigaam-tui' in text and 'export PATH="$HOME/.local/bin:$PATH"' in text
        _ensure_path(tmp_path, shell)
        assert (home / rc).read_text().count("# gigaam-tui") == 1


def test_installer_skips_rc_when_local_bin_already_in_path(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "SHELL": "/bin/zsh", "GIGAAM_INSTALL_STAGE": "path-only",
           "PATH": f"{home}/.local/bin:/usr/bin:/bin"}
    result = subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0
    assert not (home / ".zshrc").exists()


def test_installer_keeps_the_previously_selected_model(tmp_path):
    home = tmp_path / "home"
    settings_dir = home / "GigaAMTranscriber"
    settings_dir.mkdir(parents=True)
    (settings_dir / "tui_settings.json").write_text('{"model": "multilingual_ctc"}')
    env = {**os.environ, "HOME": str(home), "GIGAAM_INSTALL_STAGE": "print-model", "GIGAAM_CONFIG_DIR": str(settings_dir)}
    result = subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True, env=env, timeout=30, stdin=subprocess.DEVNULL)
    assert result.stdout.strip() == "multilingual_ctc"


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin-specific XDG-ignoring settings path")
def test_installer_settings_dir_ignores_xdg_on_darwin(tmp_path):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    settings_dir = home / "Library" / "Application Support" / "GigaAMTranscriber"
    settings_dir.mkdir(parents=True)
    (settings_dir / "tui_settings.json").write_text('{"model": "multilingual_ctc"}')
    env = {**os.environ, "HOME": str(home), "GIGAAM_INSTALL_STAGE": "print-model", "XDG_CONFIG_HOME": str(xdg)}
    env.pop("GIGAAM_CONFIG_DIR", None)
    result = subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True, env=env, timeout=30, stdin=subprocess.DEVNULL)
    assert result.stdout.strip() == "multilingual_ctc"
