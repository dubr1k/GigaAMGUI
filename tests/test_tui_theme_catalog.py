"""tui/src/theme_catalog.rs должен совпадать с выводом генератора для tui/themes/."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GENERATOR = REPO / "scripts" / "tui" / "gen_theme_catalog.py"
CATALOG = REPO / "tui" / "src" / "theme_catalog.rs"
THEMES_DIR = REPO / "tui" / "themes"


def _generator():
    spec = importlib.util.spec_from_file_location("gen_theme_catalog", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_matches_generator_output():
    gen = _generator()
    expected = gen.render(gen.theme_names())
    assert CATALOG.read_text(encoding="utf-8") == expected, (
        "tui/src/theme_catalog.rs отстал от tui/themes/: "
        "запустите scripts/tui/gen_theme_catalog.py"
    )


def test_catalog_lists_every_theme_once_sorted():
    names = _generator().theme_names()
    files = sorted(path.stem for path in THEMES_DIR.glob("*.json"))
    assert names == files
    assert len(names) == 100
    assert "dark-hermes-pink" in names
    assert names == sorted(set(names))


def test_every_theme_json_has_name_vars_colors():
    for path in THEMES_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == path.stem, path
        assert isinstance(data.get("vars"), dict), path
        assert isinstance(data.get("colors"), dict), path
        assert "accent" in data["colors"], path


def test_generator_check_mode_detects_stale_catalog(tmp_path, monkeypatch):
    gen = _generator()
    stale = tmp_path / "theme_catalog.rs"
    stale.write_text("// stale\n", encoding="utf-8")
    monkeypatch.setattr(gen, "OUTPUT", stale)
    assert gen.main(["--check"]) == 1
    assert gen.main([]) == 0
    assert stale.read_text(encoding="utf-8") == gen.render(gen.theme_names())
    assert gen.main(["--check"]) == 0
