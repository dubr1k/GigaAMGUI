"""web/static/app.js в работе: сценарии гоняются в node на заглушке DOM.

Харнесс (tests/fixtures/web_frontend_harness.js) исполняет настоящие app.js и
список провайдеров из index.html и возвращает наблюдения JSON-ом. Без node
тесты пропускаются — текстовые проверки фронтенда остаются в
test_web_app_persistence.py.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.services import cli_tools

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "fixtures" / "web_frontend_harness.js"
STATIC = ROOT / "web" / "static"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="нужен node")


def _scenario(name: str) -> dict:
    proc = subprocess.run([NODE, str(HARNESS), str(STATIC), name], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_relogin_does_not_duplicate_handlers_and_logout_closes_stream():
    result = _scenario("relogin")
    # Один клик «Запустить» — одна загрузка, даже после выхода и повторного входа без перезагрузки
    assert result["startListeners"] == 1
    assert result["uploads"] == 1
    # Выход закрывает SSE и не переподключает его к 401
    assert result["streamsAfterLogout"] == 0
    assert result["reconnectsAfterLogout"] == 0
    assert result["openStreamsAfterLogin"] == 1


def test_provider_options_keep_canonical_values():
    result = _scenario("provider_values")
    canonical = cli_tools.canonical_provider_names()
    assert [o["value"] for o in result["ru"]] == canonical
    assert [o["value"] for o in result["en"]] == canonical
    # Подпись «Другое»/«Other» — только у последнего пункта; oh-my-pi остаётся собой
    assert result["ru"][-1]["text"] == "Другое"
    assert result["en"][-1]["text"] == "Other"
    assert "oh-my-pi" in result["ru"][5]["text"] and "18.2" in result["ru"][5]["text"]
    assert result["sentProvider"] == "oh-my-pi"


def test_progress_snapshot_is_a_baseline_and_results_reload_is_debounced():
    result = _scenario("sse_baseline")
    # История при подключении не превращается в «Готово»/«Ошибка» и не дёргает /api/tasks
    assert result["afterSnapshot"] == {"logs": 0, "taskFetches": 0}
    lines = "\n".join(result["afterDelta"]["lines"])
    assert "Готово: new1.wav" in lines and "Готово: new2.wav" in lines
    assert "new3.wav — oops" in lines
    assert "line" in lines
    assert "old.wav" not in lines and "bad.wav" not in lines
    # Два завершения в одном окне — один запрос списка результатов
    assert result["afterDelta"]["taskFetches"] == 1
    # Переподключение начинается со снимка и ничего не повторяет в журнале
    assert result["newStreamOnReconnect"] is True
    assert result["replayedLines"] == 0


def test_cli_fields_follow_server_policy():
    result = _scenario("server_cli_policy")
    assert result["locked"] == [True, True, True, True]
    assert result["unlocked"] is True
    assert result["apiKeyEditable"] is True


def test_tool_statuses_are_escaped():
    html = "".join(_scenario("tools_escaped")["html"])
    assert "<img" not in html and "<b>" not in html
    assert "&lt;img" in html


def test_http_errors_are_reported():
    result = _scenario("error_paths")
    assert "Задача не найдена" in result["preview"]
    assert any("Нельзя удалить" in message for message in result["deleteAlerts"])
    assert not any("Задача удалена" in line for line in result["deleteLines"])
    assert "502" in result["llmStatus"]
    assert "JSON" not in result["llmStatus"]
