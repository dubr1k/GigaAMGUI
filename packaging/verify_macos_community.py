"""Check the frozen macOS Community-1 resource paths and isolated scripts."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESOURCES = {
    "community_worker.py": PROJECT_ROOT / "src/core/diarization/community_worker.py",
    "install_community_runtime.py": PROJECT_ROOT / "src/core/diarization/install_community_runtime.py",
    "requirements-community.txt": PROJECT_ROOT / "requirements-community.txt",
}
PATH_MARKER = "COMMUNITY_RUNTIME_PATHS="


def run(command, *, input_text=None):
    result = subprocess.run(
        [str(part) for part in command],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(
            f"{command[0]} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def verify(bundle):
    bundle = Path(bundle).resolve()
    resources = bundle / "Contents" / "Resources"
    if (resources / "src").exists():
        raise RuntimeError(f"Raw project sources must not be shipped: {resources / 'src'}")
    community = resources / "_community_runtime"
    if not community.is_dir() or {entry.name for entry in community.iterdir()} != set(RESOURCES):
        raise RuntimeError(f"Unexpected Community-1 resources in {community}")
    for name, source in RESOURCES.items():
        target = community / name
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            raise RuntimeError(f"Missing or incorrect Community-1 resource: {target}")

    worker = community / "community_worker.py"
    installer = community / "install_community_runtime.py"
    executable = bundle / "Contents" / "MacOS" / bundle.stem
    output = run([executable, "--community-runtime-path-smoke"])
    markers = [line[len(PATH_MARKER):] for line in output.splitlines() if line.startswith(PATH_MARKER)]
    if len(markers) != 1:
        raise RuntimeError(f"Frozen Community-1 path smoke did not report its paths: {output}")
    actual = json.loads(markers[0])
    expected = [worker.resolve(), installer.resolve()]
    if (
        not isinstance(actual, list)
        or len(actual) != 2
        or not all(isinstance(path, str) for path in actual)
        or [Path(path).resolve() for path in actual] != expected
    ):
        raise RuntimeError(f"Frozen Community-1 paths do not match the bundled resources: {actual}")

    response = json.loads(run(
        [sys.executable, "-I", worker], input_text='{"action":"invalid"}\n'
    ))
    if not isinstance(response, dict) or response.get("event") != "error" or response.get("kind") != "model":
        raise RuntimeError(f"Unexpected Community-1 worker response: {response}")

    # Stop at venv construction: reaching it proves that the real installer
    # located its adjacent requirements without creating an env or using pip.
    installer_probe = """
import runpy
import sys
import venv

class RequirementsFound(Exception):
    pass

def stop_before_install(*args, **kwargs):
    raise RequirementsFound

venv.EnvBuilder = stop_before_install
sys.argv = [sys.argv[1], sys.argv[2]]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except RequirementsFound:
    print("COMMUNITY_INSTALLER_REQUIREMENTS_FOUND")
else:
    raise SystemExit("Installer did not reach environment creation")
"""
    with tempfile.TemporaryDirectory(prefix="gigaam-community-probe-") as temp:
        output = run([sys.executable, "-I", "-c", installer_probe, installer, Path(temp) / "venv"])
    if output.strip() != "COMMUNITY_INSTALLER_REQUIREMENTS_FOUND":
        raise RuntimeError(f"Unexpected Community-1 installer probe response: {output}")
    print(f"Community-1 bundled resource verification passed: {bundle}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python packaging/verify_macos_community.py path/to/app.app")
    try:
        verify(sys.argv[1])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"Community-1 bundled resource verification failed: {exc}") from exc


if __name__ == "__main__":
    main()
