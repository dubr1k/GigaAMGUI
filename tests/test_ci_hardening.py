"""Токен GitHub в CI выдаётся по минимуму.

Сборочные job'ы выполняют setup.py gigaam из git и скачивают ffmpeg по
плавающей ссылке; раньше у них был workflow-wide `contents: write`, а checkout
оставлял токен в .git/config. Запись нужна только job'у публикации релиза.
Текстовые проверки — CI-окружение этих тестов ставит только pytest.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _workflow_files():
    return sorted(path for path in WORKFLOWS.glob("*.yml") if ".sync-conflict-" not in path.name)


def test_build_workflow_defaults_to_read_only_token():
    text = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
    top_level = text.split("\njobs:", 1)[0]
    assert re.search(r"^permissions:\n  contents: read$", top_level, re.M)
    assert "contents: write" not in top_level


def test_only_release_publishing_jobs_can_write():
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"contents: write", text):
            preceding = text[: match.start()]
            job = re.findall(r"^  ([a-z][a-z0-9-]*):$", preceding, re.M)
            assert job and job[-1] in {"publish-release", "publish"}, (path.name, job[-1:] )


def test_every_checkout_drops_persisted_credentials():
    for path in _workflow_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "uses: actions/checkout@" not in line:
                continue
            window = "\n".join(lines[index + 1 : index + 4])
            assert "persist-credentials: false" in window, f"{path.name}:{index + 1}"
