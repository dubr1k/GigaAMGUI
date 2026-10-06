"""save_env_value: значение не может дописать в .env чужие ключи."""

import os

import pytest
from dotenv import dotenv_values

from src.config import save_env_value


def test_newline_in_value_does_not_inject_another_key(tmp_path, monkeypatch):
    # Вставленный в поле токена текст с переводом строки дописывал в .env
    # произвольные переменные: «hf_x\nASR_BACKEND=pytorch».
    env = tmp_path / ".env"
    monkeypatch.delenv("HF_TOKEN", raising=False)

    save_env_value("HF_TOKEN", "hf_x\nASR_BACKEND=pytorch", env_path=env)

    values = dotenv_values(env)
    assert "ASR_BACKEND" not in values
    assert values["HF_TOKEN"] == "hf_x\nASR_BACKEND=pytorch"


@pytest.mark.parametrize(
    "value",
    ['quote " inside', "back\\slash", "#not-a-comment", "  spaced  ", "", "plain_token"],
)
def test_value_round_trips_through_dotenv(tmp_path, monkeypatch, value):
    env = tmp_path / ".env"
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    save_env_value("LLM_API_KEY", value, env_path=env)

    assert dotenv_values(env)["LLM_API_KEY"] == value
    assert os.environ["LLM_API_KEY"] == value


def test_other_keys_are_kept_and_old_value_replaced(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\nHF_TOKEN=old\nB=2\n", encoding="utf-8")
    monkeypatch.delenv("HF_TOKEN", raising=False)

    save_env_value("HF_TOKEN", "new", env_path=env)

    assert dotenv_values(env) == {"A": "1", "B": "2", "HF_TOKEN": "new"}


def test_invalid_key_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        save_env_value("BAD=KEY", "x", env_path=tmp_path / ".env")


def test_interrupted_write_keeps_previous_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("HF_TOKEN=old\n", encoding="utf-8")

    def failing_replace(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr("src.utils.atomic_json.os.replace", failing_replace)

    with pytest.raises(OSError):
        save_env_value("HF_TOKEN", "new", env_path=env)

    assert env.read_text(encoding="utf-8") == "HF_TOKEN=old\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == [".env"]
