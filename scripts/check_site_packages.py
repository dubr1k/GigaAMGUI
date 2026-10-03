"""Ищет в site-packages мусор, который незаметно ломает импорт и метаданные.

    python scripts/check_site_packages.py   # 0 — чисто, 1 — найдены проблемы

Syncthing синхронизирует и .venv; его конфликтные копии оставили:
- каталог пакета без ``__init__.py`` из одних ``*.sync-conflict-*`` — Python
  импортирует его как пустой namespace-пакет раньше настоящей (editable)
  установки, и бандл уезжает без кода модели;
- ``*.dist-info`` без ``METADATA`` рядом с настоящим — ``importlib.metadata``
  находит его первым и отдаёт ``None``, на чём падает проверка версий
  transformers (а с ней патч pyannote).
"""

from __future__ import annotations

import site
import sys
import sysconfig
from pathlib import Path

CONFLICT_MARK = ".sync-conflict-"


def site_package_dirs() -> list[Path]:
    candidates = {sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]}
    try:
        candidates.update(site.getsitepackages())
    except AttributeError:
        pass
    return sorted(Path(path) for path in candidates if Path(path).is_dir())


def find_problems(directory: Path) -> list[str]:
    problems: list[str] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir() or entry.name == "__pycache__":
            continue
        names = [child.name for child in entry.iterdir() if child.name != "__pycache__"]
        if entry.name.endswith(".dist-info"):
            if "METADATA" not in names:
                problems.append(f"{entry}: dist-info без METADATA ({', '.join(names[:3])}…)")
            continue
        if names and all(CONFLICT_MARK in name for name in names):
            problems.append(f"{entry}: каталог из одних конфликтных копий — затеняет пакет {entry.name}")
    return problems


def main() -> int:
    problems = [problem for directory in site_package_dirs() for problem in find_problems(directory)]
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} проблем(ы) в site-packages {sys.executable}; перенесите эти каталоги из окружения.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
