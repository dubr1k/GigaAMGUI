"""Падает, если установленная тройка torch/torchaudio/torchvision не та, что ждёт сборка.

    python scripts/check_torch_triple.py 2.6.0 2.6.0 0.21.0

Версии сравниваются без локального суффикса (+cpu, +cu124): индекс задаёт
сборка, а здесь ловится именно подмена релиза, например когда pip при
установке requirements молча откатил torch на другую ветку.
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version

PACKAGES = ("torch", "torchaudio", "torchvision")


def installed_triple() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for package in PACKAGES:
        try:
            result[package] = version(package).split("+", 1)[0]
        except PackageNotFoundError:
            result[package] = None
    return result


def main(argv: list[str]) -> int:
    if len(argv) != len(PACKAGES):
        print("Usage: python scripts/check_torch_triple.py TORCH TORCHAUDIO TORCHVISION")
        return 2
    expected = dict(zip(PACKAGES, argv, strict=True))
    actual = installed_triple()
    if actual != expected:
        print(f"Unexpected torch triple: expected {expected}, installed {actual}")
        return 1
    print(f"torch triple OK: {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
