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
