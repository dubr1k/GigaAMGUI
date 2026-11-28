"""Point the frozen Community-1 backend at its standalone macOS resources."""

def _configure_community_resources():
    import json
    import sys
    from pathlib import Path

    from src.core.diarization import community_backend, community_runtime

    # A macOS bundle must not contain the project src tree. These scripts need a
    # real path because they are run by the separate Community-1 interpreter.
    community_dir = Path(sys._MEIPASS) / "_community_runtime"
    worker = community_dir / "community_worker.py"
    installer = community_dir / "install_community_runtime.py"
    requirements = community_dir / "requirements-community.txt"
    for resource in (worker, installer, requirements):
        if not resource.is_file():
            raise RuntimeError(f"Missing bundled Community-1 resource: {resource}")

    community_backend._WORKER = worker
    community_runtime._INSTALLER = installer

    # The build check reads paths from the frozen executable before exercising
    # the worker and installer scripts with a separate interpreter.
    if sys.argv[1:] == ["--community-runtime-path-smoke"]:
        print(
            "COMMUNITY_RUNTIME_PATHS="
            + json.dumps([str(community_backend._WORKER), str(community_runtime._INSTALLER)]),
            flush=True,
        )
        raise SystemExit(0)


_configure_community_resources()
del _configure_community_resources
