"""Только PTY-тесты: настоящий resolver, управляемая имитация распознавания без ML."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from src.services import tui_input_service


def main():
    directory = Path(os.environ["GIGAAM_TEST_WORKER_DIR"])
    (directory / "worker.pid").write_text(str(os.getpid()))
    output_lock = threading.Lock()
    stopped = threading.Event()
    closing = threading.Event()
    real_resolve = tui_input_service.resolve_paths

    def emit(kind, **payload):
        with output_lock:
            print(json.dumps({"type": kind, **payload}, ensure_ascii=False), flush=True)

    def resolve(paths, cancelled):
        if os.environ.get("GIGAAM_TEST_DELAY_RESOLVE"):
            while not (directory / "release-inputs").exists():
                if cancelled():
                    return {"files": [], "duplicates": [], "errors": [], "cancelled": True}
                stopped.wait(0.02)
        return real_resolve(paths, cancelled)

    tui_input_service.resolve_paths = resolve
    resolver = tui_input_service.InputResolver(emit)

    def hello():
        while os.environ.get("GIGAAM_TEST_DELAY_HELLO") and not (directory / "release-hello").exists():
            if stopped.wait(0.02):
                return
        version = 99 if os.environ.get("GIGAAM_TEST_INVALID_HELLO") else 1
        emit("ready", protocol_version=version, capabilities=["resolve_inputs", "asr", "llm"])

    def run(command):
        if os.environ.get("GIGAAM_TEST_REJECT_AFTER_CANCEL"):
            reject_start()
            return
        if os.environ.get("GIGAAM_TEST_WORKER_DELAY_START"):
            while not (directory / "release-start").exists():
                if stopped.wait(0.02):
                    emit("completed", success=False, cancelled=True)
                    return
        if os.environ.get("GIGAAM_TEST_WORKER_FAIL_START"):
            emit("error", message="fixture initialization failed")
            return
        files = command["files"]
        emit("started", total_files=len(files), backend="fixture")
        for index, file in enumerate(files):
            if stopped.is_set():
                break
            emit("file_started", file=file, file_index=index)
            if os.environ.get("GIGAAM_TEST_PROGRESS_GATES"):
                emit("progress", file=file, stage="transcription", file_progress=0.5,
                     processed_seconds=10, total_seconds=20)
                while not (directory / f"finish-file-{index}").exists():
                    if closing.wait(0.02):
                        return
            saved = Path(file).with_suffix(".txt")
            saved.write_text("Тестовый транскрипт", encoding="utf-8")
            emit("file_completed", file=file, result={"success": True, "saved_files": [str(saved)]})
        emit("completed", success=True, cancelled=stopped.is_set())

    def reject_start():
        while not (directory / "reject-start").exists():
            if closing.wait(0.02):
                return
        # Валидация может отклонить запуск после получения отмены, без completed.
        emit("error", message="Input file does not exist")

    def long_llm_result():
        emit("llm_started", mode="summary", index=1, total=1)
        text = "Длинный ответ. " * 10000
        saved = directory / "long-summary.md"
        saved.write_text(text, encoding="utf-8")
        emit("llm_completed", success=True, saved_files=[str(saved)],
             results=[{"mode": "summary", "text": text}])

    try:
        for line in sys.stdin:
            command = json.loads(line)
            with (directory / "commands.jsonl").open("a", encoding="utf-8") as record:
                record.write(json.dumps(command, ensure_ascii=False) + "\n")
            kind = command["type"]
            if kind == "hello":
                threading.Thread(target=hello, daemon=True).start()
            elif kind == "llm_tools":
                emit("llm_tools", providers=["API"], tools=[])
            elif kind == "resolve_inputs":
                resolver.start(command)
            elif kind == "cancel_inputs":
                resolver.cancel(command["request_id"])
            elif kind == "start":
                stopped.clear()
                threading.Thread(target=run, args=(command,), daemon=True).start()
            elif kind == "cancel":
                stopped.set()
                emit("cancelling")
            elif kind == "llm_start" and os.environ.get("GIGAAM_TEST_REJECT_AFTER_CANCEL"):
                threading.Thread(target=reject_start, daemon=True).start()
            elif kind == "llm_start" and os.environ.get("GIGAAM_TEST_LONG_LLM_RESULT"):
                threading.Thread(target=long_llm_result, daemon=True).start()
            elif kind == "llm_start" and os.environ.get("GIGAAM_TEST_BLOCK_ON_LLM"):
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
                (directory / "cli-child.pid").write_text(str(child.pid))
                emit("llm_started", mode="summary", index=1, total=1)
                print("\x1b[2Junstructured stdout diagnostic", flush=True)
                print("\x1b[31mfixture stderr diagnostic\x1b[0m", file=sys.stderr, flush=True)
                # Воспроизводим движок, переставший читать stdin; остановить его
                # сможет только владелец процесса, не очередная JSON-команда.
                threading.Event().wait()
    finally:
        closing.set()
        stopped.set()
        resolver.close()


if __name__ == "__main__":
    main()
