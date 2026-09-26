"""Отмена реального локального CLI без API, моделей и чужих процессов."""
import json
import queue
import sys
import threading
import time

import psutil

from src.services import llm_service


def test_cli_cancel_terminates_owned_grandchild_and_returns_promptly(tmp_path):
    marker = tmp_path / "owned-pids.json"
    script = """
import json, os, pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(30)
"""
    cancel = threading.Event()
    result = queue.Queue()

    def run():
        try:
            result.put(llm_service._run_command([sys.executable, "-c", script, str(marker)], cancel_check=cancel.is_set))
        except Exception as error:
            result.put(error)

    thread = threading.Thread(target=run, daemon=True)
    owned = []
    try:
        thread.start()
        deadline = time.monotonic() + 5
        while not marker.exists():
            assert time.monotonic() < deadline, "test CLI did not start"
            time.sleep(0.01)
        owned = [psutil.Process(pid) for pid in json.loads(marker.read_text())]
        cancel.set()
        thread.join(timeout=3)
        assert not thread.is_alive(), "cancel stayed blocked on the grandchild's inherited stdout"
        assert isinstance(result.get_nowait(), llm_service.LLMCancelled)
        for process in owned:
            assert not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    finally:
        cancel.set()
        for process in reversed(owned):
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        thread.join(timeout=3)


def _run_in_thread(command, input_text, cancel):
    result = queue.Queue()

    def run():
        try:
            result.put(llm_service._run_command(command, input_text=input_text, cancel_check=cancel.is_set))
        except Exception as error:
            result.put(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def _kill_own_children():
    for child in psutil.Process().children(recursive=True):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass


def test_cli_receives_a_prompt_larger_than_the_pipe_buffer():
    """Промпт длинной записи больше буфера канала (~64 КБ), а CLI вроде
    `claude -p` начинает читать stdin не сразу. communicate() с таймаутом при
    повторе без input переставал дописывать промпт и не закрывал stdin — CLI
    ждал EOF вечно (зависание LLM на часовых записях)."""
    prompt = "слово " * 50_000  # ~600 КБ в UTF-8
    reader = "import sys, time; time.sleep(0.3); print(len(sys.stdin.read()))"
    cancel = threading.Event()
    thread, result = _run_in_thread([sys.executable, "-c", reader], prompt, cancel)
    try:
        thread.join(timeout=10)
        assert not thread.is_alive(), "CLI never got the whole prompt / EOF on stdin"
        completed = result.get_nowait()
        assert not isinstance(completed, Exception), completed
        assert completed.returncode == 0
        assert completed.stdout.strip() == str(len(prompt))
    finally:
        cancel.set()
        _kill_own_children()


def test_cancel_returns_while_the_prompt_is_still_being_written():
    """CLI, который не читает stdin, не должен держать отмену: запись промпта
    заблокирована на полном канале, пока процесс не остановлен."""
    prompt = "x" * 1_000_000
    sleeper = "import time; time.sleep(30)"
    cancel = threading.Event()
    thread, result = _run_in_thread([sys.executable, "-c", sleeper], prompt, cancel)
    try:
        time.sleep(0.5)
        cancel.set()
        thread.join(timeout=5)
        assert not thread.is_alive(), "cancel blocked on writing the prompt"
        assert isinstance(result.get_nowait(), llm_service.LLMCancelled)
    finally:
        cancel.set()
        _kill_own_children()
