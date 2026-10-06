"""docker-compose.yml перечисляет окружение контейнера явно.

Переменная, которой нет в списке, до web-панели не доходит вовсе, поэтому
настройки защиты проброшены с умолчаниями — и эти умолчания обязаны
совпадать с умолчаниями web/state.py, иначе «пустой» .env меняет поведение.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
STATE = (ROOT / "web" / "state.py").read_text(encoding="utf-8")


def _compose_default(name: str) -> str:
    match = re.search(rf"- {name}=\$\{{{name}:-([^}}]*)\}}", COMPOSE)
    assert match, f"{name} is not passed to the container"
    return match.group(1)


def test_web_security_settings_reach_the_container_with_code_defaults():
    assert _compose_default("WEB_ALLOW_CLIENT_LLM_CLI") == "0"
    assert _compose_default("WEB_TRUSTED_ORIGINS") == ""
    assert _compose_default("WEB_LOGIN_RATE_LIMIT") == re.search(r'or "([^"]+)"', STATE).group(1)
    assert _compose_default("WEB_MAX_CONCURRENT_LLM") == re.search(
        r'os.getenv\("WEB_MAX_CONCURRENT_LLM", "(\d+)"\)', STATE
    ).group(1)
    assert int(_compose_default("WEB_MAX_LLM_BODY_SIZE")) == 50 * 1024 * 1024
    assert "str(50 * 1024 * 1024)" in STATE


def test_container_runs_an_init_process():
    assert re.search(r"^\s+init: true$", COMPOSE, re.M)
