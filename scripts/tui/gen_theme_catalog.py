#!/usr/bin/env python3
"""Генерирует tui/src/theme_catalog.rs из tui/themes/*.json.

Каталог вшивает каждую палитру через `include_str!`, поэтому список файлов
должен быть известен на этапе компиляции. Запускать после добавления или
удаления темы; tests/test_tui_theme_catalog.py следит, чтобы файл не отстал.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
THEMES_DIR = REPO / "tui" / "themes"
OUTPUT = REPO / "tui" / "src" / "theme_catalog.rs"

HEADER = """\
//! Сгенерировано scripts/tui/gen_theme_catalog.py — не править руками.
//!
//! Палитры oh-my-pi (MIT, см. tui/themes/LICENSE-oh-my-pi) и наши собственные
//! JSON из tui/themes/, вшитые в бинарник; имя = имя файла без `.json`.

/// `("<имя>", include_str!("../themes/<имя>.json"))` — короткая строка на тему,
/// которую rustfmt не переносит, что бы ни менялось в его эвристиках ширины.
macro_rules! theme {
    ($name:literal) => {
        ($name, include_str!(concat!("../themes/", $name, ".json")))
    };
}

/// Пары «имя темы → JSON», отсортированные по имени.
pub(crate) static THEMES: &[(&str, &str)] = &[
"""

FOOTER = "];\n"


def theme_names(themes_dir: Path = THEMES_DIR) -> list[str]:
    """Имена тем: `*.json` в каталоге, чьё поле `name` совпадает с именем файла."""
    names = []
    for path in sorted(themes_dir.glob("*.json")):
        name = path.stem
        with path.open(encoding="utf-8") as handle:
            declared = json.load(handle).get("name")
        if declared != name:
            raise SystemExit(f"{path}: JSON name {declared!r} != file name {name!r}")
        names.append(name)
    return names


def render(names: list[str]) -> str:
    rows = "".join(
        f'    theme!("{name}"),\n' for name in names
    )
    return HEADER + rows + FOOTER


def main(argv: list[str]) -> int:
    text = render(theme_names())
    if "--check" in argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print(f"{OUTPUT} is stale; run {Path(__file__).name}", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"{OUTPUT}: {text.count('theme!(')} themes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
