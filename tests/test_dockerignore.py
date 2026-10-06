"""Контекст Docker-сборки не должен уносить секреты и локальный мусор в образ.

Dockerfile делает `COPY . /app`, поэтому всё, что пропустил .dockerignore,
навсегда остаётся в слоях образа. Тест повторяет семантику moby/patternmatcher:
файл исключён, если последний совпавший шаблон не отрицательный, а шаблон
совпадает с самим путём или с любым его родительским каталогом.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _to_regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def _patterns() -> list[tuple[bool, re.Pattern[str]]]:
    rules = []
    for raw in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        rules.append((negated, _to_regex(line.lstrip("!").strip("/"))))
    return rules


def _excluded(path: str, rules) -> bool:
    parts = path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    excluded = False
    for negated, rx in rules:
        if any(rx.match(candidate) for candidate in candidates):
            excluded = not negated
    return excluded


SECRETS_AND_LOCAL_STATE = [
    ".env",
    ".env.auth-backup-20260621-184919",
    ".env.example",
    ".api_keys",
    ".deploy-backups/2026-07-01/.env",
    ".hermes/config.json",
    ".claude/settings.local.json",
    ".git/config",
    "api.log",
    "tmp/upload.wav",
    "bin/ffmpeg",
    "docker-compose.override.yml",
    "offline/models/hf/refs/main",
    "uploads/x.wav",
    "results/task/x.txt",
    "src/.env",
    "src/services/.api_keys",
    "src/gui/app_qt.py",
    "src/gigaam/gigaam/model.py",
    "src/core/__pycache__/processor.cpython-310.pyc",
    "src/core/processor.sync-conflict-20260906-162524-3LI4UIX.py",
    "web/web_app.sync-conflict-20260906-162524-3LI4UIX.py",
    "tests/test_dockerignore.py",
    "tui/Cargo.toml",
    "macos/GigaAMLiquid/Package.swift",
]

RUNTIME_FILES = [
    "src/__init__.py",
    "src/config.py",
    "src/core/processor.py",
    "src/services/api_keys.py",
    "src/services/mcp_backend.py",
    "web/web_app.py",
    "web/static/app.js",
    "web/static/index.html",
    "api.py",
    "cli.py",
    "docker-entrypoint.sh",
    "requirements.txt",
    "requirements-sortformer.txt",
]


def test_secrets_and_local_state_never_reach_the_build_context():
    rules = _patterns()
    leaked = [path for path in SECRETS_AND_LOCAL_STATE if not _excluded(path, rules)]
    assert leaked == []


def test_runtime_sources_are_in_the_build_context():
    rules = _patterns()
    missing = [path for path in RUNTIME_FILES if _excluded(path, rules)]
    assert missing == []


def test_dockerignore_is_an_allowlist():
    # Denylist однажды уже пропустил .api_keys и бэкапы .env: всё новое в корне
    # репозитория должно по умолчанию оставаться вне образа.
    rules = _patterns()
    assert rules and rules[0][0] is False and rules[0][1].pattern == "^[^/]*$"
    assert _excluded("some-new-local-file.txt", rules)


def test_dockerfile_copies_only_through_the_context():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copies = [line for line in dockerfile.splitlines() if line.strip().upper().startswith("COPY")]
    rules = _patterns()
    for line in copies:
        sources = line.split()[1:-1]
        for source in sources:
            if source != ".":
                assert not _excluded(source, rules), line
