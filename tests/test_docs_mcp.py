"""Документация MCP покрывает контракт сервера.

Имена tools/resources/prompts берутся из собранного `build_server` (регистр
SDK), а не из захардкоженного списка: новый инструмент без строки в
`docs/MCP.md` и в скилле `skills/gigaam-mcp/SKILL.md` ломает тест.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from src.services.mcp_server import build_server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MCP_DOC = ROOT / "docs" / "MCP.md"
MCP_SKILL = ROOT / "skills" / "gigaam-mcp" / "SKILL.md"
CLI_SKILL = ROOT / "skills" / "gigaam" / "SKILL.md"


class _StubBackend:
    """`build_server` не трогает бэкенд при регистрации; методы нужны только для формы."""

    async def transcribe(self, **kwargs):  # pragma: no cover — не вызывается
        raise NotImplementedError

    async def summarize(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def models(self):  # pragma: no cover
        return {}

    def llm_providers(self):  # pragma: no cover
        return {}

    def status(self):  # pragma: no cover
        return {}


def _registered_names() -> dict[str, set[str]]:
    server = build_server(_StubBackend())

    async def collect():
        tools = {t.name for t in await server.list_tools()}
        resources = {str(r.uri) for r in await server.list_resources()}
        prompts = {p.name for p in await server.list_prompts()}
        return {"tools": tools, "resources": resources, "prompts": prompts}

    return asyncio.run(collect())


@pytest.fixture(scope="module")
def names() -> dict[str, set[str]]:
    return _registered_names()


def _read(path: Path) -> str:
    assert path.is_file(), f"missing {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_registry_is_not_empty(names):
    assert names["tools"] >= {"transcribe", "summarize", "server_status"}
    assert names["resources"] and names["prompts"]


@pytest.mark.parametrize("doc", [MCP_DOC, MCP_SKILL], ids=["docs/MCP.md", "skills/gigaam-mcp"])
def test_reference_covers_every_tool_resource_and_prompt(names, doc):
    text = _read(doc)
    missing = [name for group in ("tools", "resources", "prompts") for name in sorted(names[group])
               if f"`{name}`" not in text]
    assert not missing, f"{doc.relative_to(ROOT)} lacks: {missing}"


def test_mcp_doc_has_client_snippets_and_remote_form():
    text = _read(MCP_DOC)
    for needle in (
        "claude mcp add gigaam -- gigaam mcp",       # Claude Code, stdio
        "~/.codex/config.toml", "[mcp_servers.gigaam]",  # Codex
        ".cursor/mcp.json",                           # Cursor
        "--transport http",                           # Claude Code, remote
        "https://gigaam-site.dubr1k.space/mcp",
        "Authorization: Bearer",
        "GIGAAM_MCP_ALLOW_PATHS", "GIGAAM_MCP_PATH_ROOT", "GIGAAM_MCP_MAX_INLINE_MB", "API_KEYS_FILE",
    ):
        assert needle in text, needle


def test_mcp_skill_frontmatter_and_source_rules():
    text = _read(MCP_SKILL)
    assert text.startswith("---\nname: gigaam-mcp\n")
    assert "description:" in text.split("---")[1]
    for needle in ("`url`", "`path`", "`audio_base64`", "GIGAAM_MCP_ALLOW_PATHS", "max_inline_mb",
                   "[file_too_large]", "[paths_not_allowed]", "[model_not_found]", "Authorization: Bearer"):
        assert needle in text, needle


@pytest.mark.parametrize("path", [CLI_SKILL, ROOT / "README.md", ROOT / "README_EN.md"],
                         ids=["skills/gigaam", "README.md", "README_EN.md"])
def test_overviews_mention_the_launcher_command_and_the_mount(path):
    text = _read(path)
    assert "gigaam mcp" in text
    assert "/mcp" in text


def test_launcher_and_installer_know_both_skills():
    launcher = _read(ROOT / "scripts" / "tui" / "gigaam-launcher.sh")
    assert "mcp)" in launcher and "-m src.mcp_server" in launcher
    assert "skills/gigaam-mcp" in launcher or "gigaam-mcp" in launcher
    installer = _read(ROOT / "scripts" / "install_tui.sh")
    assert "claude mcp add gigaam -- gigaam mcp" in installer
