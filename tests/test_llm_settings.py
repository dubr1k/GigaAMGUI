"""llm_settings.resolve(): overrides > env > user_settings.json > tui_settings.json > defaults."""
import json

from src.services import llm_settings


def test_defaults_when_nothing_configured(tmp_path):
    out = llm_settings.resolve(config_dir=tmp_path, env={})
    assert out["provider"] == "API" and out["temperature"] == 0.2 and out["api_key"] == ""
    assert out["claude_path"] and out["codex_path"] and out["llm_allow_tools"] is False


def test_user_settings_beat_tui_settings(tmp_path):
    (tmp_path / "user_settings.json").write_text(json.dumps({"llm_provider": "Claude Code", "llm_model": "sonnet"}))
    (tmp_path / "tui_settings.json").write_text(json.dumps({"llm_provider": "Codex", "llm_model": "o3"}))
    out = llm_settings.resolve(config_dir=tmp_path, env={})
    assert out["provider"] == "Claude Code" and out["model"] == "sonnet"


def test_env_beats_files_and_dotenv_supplies_key(tmp_path):
    (tmp_path / "user_settings.json").write_text(json.dumps({"llm_provider": "API", "llm_model": "a"}))
    (tmp_path / ".env").write_text("LLM_API_KEY=sk-from-dotenv\n")
    out = llm_settings.resolve(config_dir=tmp_path, env={"LLM_MODEL": "b", "LLM_TEMPERATURE": "0.7"})
    assert out["model"] == "b" and out["temperature"] == 0.7 and out["api_key"] == "sk-from-dotenv"
    out = llm_settings.resolve(config_dir=tmp_path, env={"LLM_API_KEY": "sk-env"})
    assert out["api_key"] == "sk-env"


def test_overrides_win(tmp_path):
    out = llm_settings.resolve({"provider": "Other", "model": "x"}, config_dir=tmp_path, env={"LLM_PROVIDER": "API"})
    assert out["provider"] == "Other" and out["model"] == "x"


def test_tui_settings_maps_and_desktop_flat_keys_mirror_the_tui(tmp_path):
    # tui_settings.json хранит словари по префиксам, user_settings.json — плоские llm_<prefix>_* ключи
    (tmp_path / "tui_settings.json").write_text(json.dumps({
        "llm_tool_paths": {"claude": "/tui/claude", "opencode": "/tui/opencode"},
        "llm_internal_providers": {"pi": "google"},
        "llm_extra_args": {"codex": "--tui"},
        "llm_allow_tools": True,
    }))
    out = llm_settings.resolve(config_dir=tmp_path, env={})
    assert out["claude_path"] == "/tui/claude" and out["pi_provider"] == "google" and out["codex_args"] == "--tui"
    assert out["llm_allow_tools"] is True

    (tmp_path / "user_settings.json").write_text(json.dumps({
        "llm_temperature": "0.5",            # PyQt хранит строкой
        "llm_claude_path": "claude",         # голое имя бинаря — не override, сбрасывает tui-путь
        "llm_opencode_path": "",             # пусто — тоже сброс
        "llm_omp_provider": "anthropic", "llm_omp_args": "--thinking low",
        "llm_other_path": "/x/llm", "llm_allow_tools": False,
    }))
    out = llm_settings.resolve(config_dir=tmp_path, env={})
    assert out["temperature"] == 0.5 and out["llm_allow_tools"] is False
    assert out["claude_path"] != "/tui/claude" and out["opencode_path"] != "/tui/opencode"
    assert out["omp_provider"] == "anthropic" and out["omp_args"] == "--thinking low" and out["other_path"] == "/x/llm"
    assert out["pi_provider"] == "google"  # ключ, которого нет в user_settings, остаётся из tui_settings


def test_bad_values_fall_through(tmp_path):
    (tmp_path / "user_settings.json").write_text("{not json")
    (tmp_path / "tui_settings.json").write_text(json.dumps({"llm_temperature": "hot", "llm_model": "m"}))
    out = llm_settings.resolve(config_dir=tmp_path, env={"LLM_TEMPERATURE": "warm"})
    assert out["temperature"] == 0.2 and out["model"] == "m"


def test_config_dir_honours_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "cfg"))
    assert llm_settings.config_dir() == tmp_path / "cfg"
