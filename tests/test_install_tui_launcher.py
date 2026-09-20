"""Лаунчер ~/.local/bin/gigaam: --update/--version/--help и запуск бинаря."""
import os
import stat
import subprocess
from pathlib import Path

LAUNCHER = Path("scripts/tui/gigaam-launcher.sh").resolve()


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
