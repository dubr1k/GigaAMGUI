"""Одна и та же зависимость закреплена в нескольких местах — версии обязаны совпадать.

Ревизия GigaAM живёт в восьми файлах (requirements, Dockerfile, CI, сборка
macOS, установщики TUI); расхождение уже ломало диаризацию в Docker (старая
ревизия без `_decode(word_timestamps=True)` схлопывала говорящих в одного).
Тройка torch в CI обязана совпадать с тем, что реально уезжает в бандл и
скачивается рантаймом по умолчанию. Только текстовые проверки: CI-окружение
этих тестов ставит лишь pytest.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GIGAAM_PIN_FILES = [
    "requirements.txt",
    "Dockerfile",
    ".github/workflows/build.yml",
    "packaging/build_exe_mac.sh",
    "scripts/install_tui.sh",
    "distribution/homebrew/Formula/gigaam-tui.rb",
    "distribution/npm/gigaam-tui/bin/gigaam.js",
]
_SHA = re.compile(r"[0-9a-f]{40}")


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _reference_sha(text: str, repo: str) -> list[str]:
    shas = re.findall(rf"{re.escape(repo)}(?:\.git)?@({_SHA.pattern})", text)
    # Dockerfile клонирует репозиторий и делает отдельный `git checkout <sha>`.
    shas += re.findall(rf"git checkout ({_SHA.pattern})", text) if repo.endswith("GigaAM") else []
    return shas


def test_gigaam_revision_is_the_same_everywhere():
    expected = _reference_sha(_read("requirements.txt"), "salute-developers/GigaAM")
    assert len(expected) == 1, expected
    for relative in GIGAAM_PIN_FILES:
        found = _reference_sha(_read(relative), "salute-developers/GigaAM")
        assert found, f"{relative}: GigaAM revision not found"
        assert set(found) == set(expected), (relative, found, expected)


def test_gigaam_mlx_revision_is_the_same_everywhere():
    expected = _reference_sha(_read("requirements-macos-mlx.txt"), "aystream/gigaam-mlx")
    assert len(expected) == 1, expected
    found = _reference_sha(_read("packaging/build_exe_mac.sh"), "aystream/gigaam-mlx")
    assert set(found) == set(expected)


def _runtime_default_triple() -> tuple[str, str, str]:
    source = _read("src/utils/runtime_manager.py")
    block = re.search(r"_TORCH_26_STACK = \{(.*?)\}", source, re.S).group(1)
    versions = dict(re.findall(r'"(torch|torchaudio|torchvision)": "([^"]+)"', block))
    # cpu, cu124 и macOS default ссылаются на этот стек.
    for variant in ("cpu", "cu124", "default"):
        pattern = rf'"{variant}": \{{\s*"index": "[^"]+",[^\n]*\n\s*"packages": _TORCH_26_STACK'
        assert re.search(pattern, source), variant
    return versions["torch"], versions["torchaudio"], versions["torchvision"]


def test_build_time_torch_matches_runtime_default_and_docker():
    expected = _runtime_default_triple()
    workflow = _read(".github/workflows/build.yml")
    installs = re.findall(
        r'"torch==([^"]+)" "torchaudio==([^"]+)" "torchvision==([^"]+)"', workflow
    )
    checks = re.findall(r"check_torch_triple\.py (\S+) (\S+) (\S+)", workflow)
    assert len(installs) == 2 and len(checks) == 2
    assert set(installs) == {expected}
    assert set(checks) == {expected}

    dockerfile = _read("Dockerfile")
    docker_triple = re.search(
        r"torch==(\S+) torchaudio==(\S+) torchvision==(\S+)", dockerfile
    ).groups()
    assert docker_triple == expected


def test_requirements_do_not_pin_sympy():
    # torch 2.6 требует sympy==1.13.1, torch 2.8 — >=1.13.3: пин превращал
    # `torch>=2.6,<2.9` в «только 2.6» и молча откатывал cu128/Blackwell.
    pinned = [line for line in _read("requirements.txt").splitlines() if re.match(r"\s*sympy\s*[=<>~!]", line)]
    assert pinned == []
