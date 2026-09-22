"""The TUI colour themes are documented where users look: READMEs, CHANGELOG, release notes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    stop = text.find(end, begin + len(start))
    return text[begin : stop if stop != -1 else len(text)]


def test_readmes_describe_theme_command_and_attribution():
    for rel in ("README.md", "README_EN.md"):
        text = _read(rel)
        for needle in ("/theme", "--theme", "oh-my-pi", "LICENSE-oh-my-pi",
                       "dark-hermes-pink", "mono", "tui_settings.json"):
            assert needle in text, f"{rel}: {needle}"


def test_changelog_2_5_0_mentions_themes():
    section = _section(_read("docs/CHANGELOG.md"), "## [2.5.0]", "\n## [")
    assert "/theme" in section
    assert "oh-my-pi" in section


def test_release_notes_mention_themes_in_both_languages():
    text = _read("docs/RELEASE_NOTES_2.5.0.md")
    russian = _section(text, "## Добавлено", "## English")
    english = _section(text, "## English", "\n\n\n\n")
    for half, name in ((russian, "ru"), (english, "en")):
        assert "/theme" in half, name
        assert "oh-my-pi" in half, name
    assert "/theme dark-hermes-pink" in _section(text, "## Что проверить", "## English")
    assert "/theme dark-hermes-pink" in _section(text, "### What to check", "\n\n\n\n")


def test_theme_count_matches_the_bundled_catalogue():
    count = len(list((ROOT / "tui" / "themes").glob("*.json"))) + 2  # + default, mono
    for rel, phrase in (("README.md", f"из {count} схем"), ("README_EN.md", f"{count} schemes")):
        assert phrase in _read(rel), f"{rel}: README must say {count} schemes (files + default + mono)"
