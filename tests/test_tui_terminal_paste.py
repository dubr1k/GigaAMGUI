"""Сквозные тесты TUI: проверяется текущая сетка экрана, не история stdout."""
import json
import os
import signal
import sys

import pytest

from tests.tui_terminal_driver import TerminalSession

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="requires a Unix PTY")


@pytest.fixture
def terminal_factory(tmp_path):
    binary = os.environ.get("GIGAAM_TUI_TEST_BINARY")
    if not binary:
        pytest.skip("set GIGAAM_TUI_TEST_BINARY to the built TUI")
    sessions = []

    def create(**environment):
        terminal = TerminalSession(binary, tmp_path, **environment)
        sessions.append(terminal)
        terminal.wait(lambda t: "GigaAM TUI 2.0.1" in t.text)
        return terminal

    yield create
    for terminal in sessions:
        terminal.close()


def escaped(path):
    return str(path).replace("\\", "\\\\").replace(" ", "\\ ").replace("(", "\\(").replace(")", "\\)")


def commands(directory, kind):
    record = directory / "commands.jsonl"
    if not record.exists():
        return []
    return [value for line in record.read_text().splitlines()
            if (value := json.loads(line))["type"] == kind]


def drop(terminal, path, bracketed=True):
    payload = escaped(path)
    terminal.paste(payload) if bracketed else terminal.send(payload)


def queued(terminal, count):
    terminal.wait(lambda t: f"Очередь ({count})" in t.queue_text)


def test_unresponsive_worker_diagnostics_and_owned_descendant_force_stop(tmp_path, terminal_factory):
    import psutil

    terminal = terminal_factory(GIGAAM_TEST_BLOCK_ON_LLM="1")
    # Заголовок появляется до handshake; запуск до ready закономерно отклоняется.
    terminal.wait(lambda t: "● Готово" in t.text)
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("fixture", encoding="utf-8")
    terminal.command(f"/llm-file {transcript}")
    terminal.send("\x1bOQ\x1b[17~")  # F2, F6
    terminal.wait(lambda t: (tmp_path / "cli-child.pid").exists() and "LLM…" in t.text)
    owned = [psutil.Process(int((tmp_path / name).read_text()))
             for name in ("worker.pid", "cli-child.pid")]

    def gone(process):
        return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE

    try:
        terminal.send("\x1bOS")  # F4: diagnostics, not terminal escape execution
        terminal.wait(lambda t: "fixture stderr diagnostic" in t.text and "unstructured stdout diagnostic" in t.text)
        assert "GigaAM TUI" in terminal.text
        terminal.send("\x1bOQ\x1b")
        terminal.wait(lambda t: "[Прервать сейчас]" in t.text)
        terminal.send("\x1b")
        terminal.wait(lambda t: "[Y · Да]" in t.text)
        terminal.send("n")
        terminal.wait(lambda t: "[Y · Да]" not in t.text)
        assert all(not gone(process) for process in owned)
        terminal.send("\x1b")
        terminal.wait(lambda t: "[Y · Да]" in t.text)
        terminal.send("y")
        terminal.wait(lambda t: int((tmp_path / "worker.pid").read_text()) != owned[0].pid and "Готово" in t.text)
        terminal.wait(lambda t: all(gone(process) for process in owned))
        assert len(commands(tmp_path, "llm_start")) == 1
    finally:
        # При падении проверки зависшая fixture не читает EOF; убираем только её PID.
        for process in owned:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass


def test_two_file_progress_and_saved_results_survive_clear(tmp_path, terminal_factory):
    terminal = terminal_factory(GIGAAM_TEST_PROGRESS_GATES="1")
    for index in range(2):
        path = tmp_path / f"Запись {index}.wav"
        path.touch()
        drop(terminal, path)
        queued(terminal, index + 1)
    terminal.send("s")
    terminal.wait(lambda t: "Всего 25%" in t.text and "Файл 1/2: 50%" in t.text)
    (tmp_path / "finish-file-0").touch()
    terminal.wait(lambda t: "Всего 75%" in t.text and "Файл 2/2: 50%" in t.text)
    (tmp_path / "finish-file-1").touch()
    terminal.wait(lambda t: "Готово 2" in t.text and "Всего 100%" in t.text)
    terminal.click("[Очистить]")
    terminal.wait(lambda t: "Перетащите или вставьте" in t.queue_text)
    terminal.send("\x1b[20~")  # F9
    terminal.wait(lambda t: "Результаты (2)" in t.text)
    assert "Запись 0.txt" in terminal.text and "Запись 1.txt" in terminal.text
    terminal.send("\x1b[B")
    terminal.wait(lambda t: "Полный путь" in t.text and "Запись 1.txt" in t.text)
    # Диагностика пропавшего файла, без запуска настоящего внешнего приложения.
    (tmp_path / "Запись 1.txt").unlink()
    terminal.send("\r")
    terminal.wait(lambda t: "Не удалось открыть" in t.text)
    terminal.send("\x1b")
    terminal.wait(lambda t: "┌ Результаты (2)" not in t.text)
    assert terminal.process.poll() is None


@pytest.mark.parametrize("broken_worker", [False, True])
def test_quit_restores_terminal_after_normal_or_broken_worker(tmp_path, terminal_factory, broken_worker):
    import termios

    terminal = terminal_factory()
    terminal.wait(lambda t: "Готово" in t.text)
    worker_pid = int((tmp_path / "worker.pid").read_text())
    raw_flags = termios.tcgetattr(terminal.master)[3]
    assert not raw_flags & (termios.ICANON | termios.ECHO)
    if broken_worker:
        os.kill(worker_pid, signal.SIGKILL)
        terminal.wait(lambda t: "недоступен" in t.text)
    terminal.send("q")
    terminal.wait(lambda t: t.process.poll() is not None)
    assert terminal.process.returncode == 0
    assert termios.tcgetattr(terminal.master) == terminal.initial_termios
    for sequence in (b"\x1b[?25h", b"\x1b[?2004l", b"\x1b[?1049l"):
        assert sequence in terminal.output
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


@pytest.mark.parametrize("bracketed", [True, False], ids=["paste-event", "cmux-keystrokes"])
def test_terminal_queue_lifecycle(tmp_path, terminal_factory, bracketed):
    terminal = terminal_factory()
    names = ("Ректорат 07.09 (1).mp3", "Ректорат 07.09 (1)\u00a0— копия.mp3")
    for count, name in enumerate(names, 1):
        path = tmp_path / name
        path.touch()
        drop(terminal, path, bracketed)
        queued(terminal, count)
        assert name in terminal.queue_text
        assert not any(line.startswith("Файлы:") for line in terminal.screen.lines)

    concatenated = [tmp_path / name for name in ("Третья (3).mp3", "Четвёртая (4).mp3")]
    for path in concatenated:
        path.touch()
    terminal.send("".join(escaped(path) for path in concatenated))
    queued(terminal, 4)
    assert all(path.name in terminal.queue_text for path in concatenated), terminal.text

    drop(terminal, tmp_path / names[0])
    terminal.wait(lambda t: "уже в очереди: 1" in t.text)
    queued(terminal, 4)

    fifth = tmp_path / "Пятый.wav"
    fifth.touch()
    terminal.paste(f"{escaped(fifth)} {escaped(tmp_path / 'missing.wav')}")
    queued(terminal, 5)
    terminal.wait(lambda t: "ошибок: 1" in t.text)
    assert fifth.name in terminal.queue_text

    folder = tmp_path / "Папка с записями"
    (folder / "nested").mkdir(parents=True)
    (folder / "шестой.wav").touch()
    (folder / "nested" / "седьмой.mp3").touch()
    (folder / "ignored.md").write_text("not media")
    drop(terminal, folder)
    queued(terminal, 7)
    assert "седьмой.mp3" in terminal.queue_text and "шестой.wav" in terminal.queue_text
    assert "ignored.md" not in terminal.queue_text

    terminal.send("\x1b[3~")  # Delete
    queued(terminal, 6)
    terminal.send("\x1a")  # Ctrl+Z
    queued(terminal, 7)
    terminal.resize(80, 24)
    terminal.wait(lambda t: "[Добавить]" in t.text and "[Действия]" in t.text)
    terminal.resize(160, 40)
    terminal.wait(lambda t: "седьмой.mp3" in t.queue_text)

    terminal.send("\x1b[15~")  # F5
    terminal.wait(lambda t: "Обработка завершена" in t.text)
    starts = commands(tmp_path, "start")
    assert len(starts) == 1 and len(starts[0]["files"]) == 7

    new_file = tmp_path / "Новый.wav"
    new_file.touch()
    drop(terminal, new_file)
    queued(terminal, 8)
    terminal.send("\x1b[15~")
    terminal.wait(lambda t: len(commands(tmp_path, "start")) == 2 and "Обработка завершена" in t.text)
    assert commands(tmp_path, "start")[1]["files"] == [str(new_file.resolve())]

    terminal.command("/clear")
    terminal.wait(lambda t: "Очередь (" not in t.queue_text and "появятся здесь" in t.queue_text)
    terminal.send("\x1b[20~")
    terminal.wait(lambda t: "┌ Результаты (8)" in t.text)
    assert "Новый.txt" in terminal.text
    assert new_file.with_suffix(".txt").is_file()
    terminal.send("\x1b")
    terminal.wait(lambda t: "┌ Результаты (8)" not in t.text)
    terminal.send("q")
    terminal.wait(lambda t: b"\x1b[?2004l" in t.output)
    terminal.process.wait(timeout=5)
    assert terminal.process.returncode == 0


def test_terminal_start_lock_and_worker_death(tmp_path, terminal_factory):
    terminal = terminal_factory(GIGAAM_TEST_WORKER_DELAY_START="1")
    path = tmp_path / "delayed.wav"
    path.touch()
    drop(terminal, path)
    queued(terminal, 1)
    terminal.send("\x1b[15~")
    terminal.wait(lambda t: "Запускаем обработку" in t.text)
    terminal.send("\x1b[15~q\x1b[3~")
    terminal.send("\x1bOR")  # F3 remains available while Starting
    terminal.wait(lambda t: "Настройки" in t.text and "Запускаем обработку" in t.text)
    assert terminal.process.poll() is None
    terminal.send("\x1bOP")  # F1
    terminal.wait(lambda t: "delayed.wav" in t.queue_text)
    assert len(commands(tmp_path, "start")) == 1
    worker_pid = int((tmp_path / "worker.pid").read_text())
    os.kill(worker_pid, signal.SIGKILL)
    terminal.wait(lambda t: "недоступен" in t.text or "завершился" in t.text)
    terminal.send("\x1b[15~")
    assert len(commands(tmp_path, "start")) == 1
    assert "delayed.wav" in terminal.queue_text
    terminal.send("q")
    terminal.wait(lambda t: b"\x1b[?2004l" in t.output)


@pytest.mark.parametrize("cancel", ["clear", "escape"])
def test_terminal_cancel_pending_folder_then_add_new_input(tmp_path, terminal_factory, cancel):
    terminal = terminal_factory(GIGAAM_TEST_DELAY_RESOLVE="1")
    folder = tmp_path / "slow"
    folder.mkdir()
    (folder / "old.wav").touch()
    drop(terminal, folder)
    terminal.wait(lambda t: "Добавляем файлы" in t.text)
    if cancel == "clear":
        terminal.command("/clear")
        terminal.wait(lambda t: "Очередь очищена" in t.text)
    else:
        terminal.send("\x1b")
        terminal.wait(lambda t: "Добавление отменено" in t.text)
    new_file = tmp_path / "new.wav"
    new_file.touch()
    drop(terminal, new_file)
    terminal.wait(lambda t: len(commands(tmp_path, "resolve_inputs")) == 2)
    (tmp_path / "release-inputs").touch()
    queued(terminal, 1)
    assert "new.wav" in terminal.queue_text
    assert "old.wav" not in terminal.queue_text
    assert len(commands(tmp_path, "cancel_inputs")) == 1


@pytest.mark.parametrize("quote", ['"', "'"])
def test_terminal_single_quoted_path_reaches_real_resolver(tmp_path, terminal_factory, quote):
    terminal = terminal_factory()
    path = tmp_path / "Путь с пробелами.wav"
    path.touch()
    terminal.paste(f"{quote}{path}{quote}")
    queued(terminal, 1)
    assert path.name in terminal.queue_text
    assert commands(tmp_path, "resolve_inputs")[0]["paths"] == [str(path)]


def test_terminal_start_error_returns_to_idle(tmp_path, terminal_factory):
    terminal = terminal_factory(GIGAAM_TEST_WORKER_FAIL_START="1")
    path = tmp_path / "failed.wav"
    path.touch()
    drop(terminal, path)
    queued(terminal, 1)
    terminal.send("\x1b[15~")
    terminal.wait(lambda t: "fixture initialization failed" in t.text)
    assert "Готово" in terminal.text
    terminal.send("q")
    terminal.wait(lambda t: b"\x1b[?2004l" in t.output)


@pytest.mark.parametrize("llm", [False, True])
def test_terminal_rejected_start_after_cancel_returns_to_idle(tmp_path, terminal_factory, llm):
    terminal = terminal_factory(GIGAAM_TEST_REJECT_AFTER_CANCEL="1")
    terminal.wait(lambda t: "Готово" in t.text)
    path = tmp_path / ("rejected.txt" if llm else "rejected.wav")
    path.touch()
    if llm:
        terminal.command(f"/llm-file {path}")
        terminal.send("\x1bOQ\x1b[17~")
    else:
        drop(terminal, path)
        queued(terminal, 1)
        terminal.send("\x1b[15~")
    start, cancel = ("llm_start", "llm_cancel") if llm else ("start", "cancel")
    terminal.wait(lambda t: len(commands(tmp_path, start)) == 1)
    terminal.send("\x1b")
    terminal.wait(lambda t: len(commands(tmp_path, cancel)) == 1)
    if llm:
        terminal.send("\x1bOS")  # F4: ошибка worker доступна в журнале
    (tmp_path / "reject-start").touch()
    terminal.wait(lambda t: "Input file does not exist" in t.text and "Готово" in t.text)
    terminal.send("\x1b[17~" if llm else "\x1b[15~")
    terminal.wait(lambda t: len(commands(tmp_path, start)) == 2 and "Готово" in t.text)
    assert len(commands(tmp_path, cancel)) == 1


def test_terminal_long_llm_completion_unlocks_and_preserves_saved_result(tmp_path, terminal_factory):
    terminal = terminal_factory(GIGAAM_TEST_LONG_LLM_RESULT="1")
    terminal.wait(lambda t: "Готово" in t.text)
    path = tmp_path / "transcript.txt"
    path.write_text("fixture", encoding="utf-8")
    terminal.command(f"/llm-file {path}")
    terminal.send("\x1bOQ\x1b[17~")
    terminal.wait(lambda t: "Длинный ответ." in t.text and "Готово" in t.text)
    terminal.send("\x1b[20~")
    terminal.wait(lambda t: "Результаты (1)" in t.text and "long-summary.md" in t.text)
    assert (tmp_path / "long-summary.md").stat().st_size > 65536
    terminal.send("\x1b")
    terminal.wait(lambda t: "┌ Результаты (1)" not in t.text)
    terminal.send("\x1b[17~")
    terminal.wait(lambda t: len(commands(tmp_path, "llm_start")) == 2 and "Готово" in t.text)


def test_terminal_llm_is_locked_before_ack_and_force_stop_needs_confirmation(tmp_path, terminal_factory):
    terminal = terminal_factory()
    terminal.wait(lambda t: "Готово" in t.text)
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("Тест", encoding="utf-8")
    terminal.command(f"/llm-file {transcript}")
    terminal.send("\x1bOQ")  # F2
    terminal.wait(lambda t: "transcript.txt" in t.text)
    terminal.send("\x1b[17~")  # F6
    terminal.wait(lambda t: "Запускаем LLM" in t.text)
    terminal.send("\x1b[17~")
    terminal.pump(timeout=0.2)
    assert len(commands(tmp_path, "llm_start")) == 1
    original_pid = (tmp_path / "worker.pid").read_text()
    terminal.send("\x1b")
    terminal.wait(lambda t: len(commands(tmp_path, "llm_cancel")) == 1)
    terminal.send("\x1b")
    terminal.wait(lambda t: "Прервать сейчас?" in t.text)
    assert (tmp_path / "worker.pid").read_text() == original_pid
    terminal.send("\x1b")
    terminal.wait(lambda t: "Прервать сейчас?" not in t.text)
    assert len(commands(tmp_path, "llm_cancel")) == 1
    terminal.send("\x1b")
    terminal.wait(lambda t: "Прервать сейчас?" in t.text)
    terminal.send("y")
    terminal.wait(lambda t: "Готово" in t.text and (tmp_path / "worker.pid").read_text() != original_pid)
    assert len(commands(tmp_path, "llm_start")) == 1
    assert "transcript.txt" in terminal.text


def test_terminal_delayed_and_invalid_hello_keep_interface_usable(tmp_path, terminal_factory):
    terminal = terminal_factory(GIGAAM_TEST_DELAY_HELLO="1")
    terminal.wait(lambda t: "Подключаем движок" in t.text)
    terminal.send("\x1b[15~\x1b[17~")
    terminal.send("\x1bOR")
    terminal.wait(lambda t: "Настройки" in t.text)
    assert not commands(tmp_path, "start")
    assert not commands(tmp_path, "llm_start")
    assert not commands(tmp_path, "llm_tools")
    (tmp_path / "release-hello").touch()
    terminal.wait(lambda t: "Готово" in t.text)
    assert len(commands(tmp_path, "llm_tools")) == 1


def test_terminal_invalid_hello_is_reported(tmp_path, terminal_factory):
    terminal = terminal_factory(GIGAAM_TEST_INVALID_HELLO="1")
    terminal.wait(lambda t: "Несовместимый протокол" in t.text)
    assert "Переподключить" in terminal.text
    assert not commands(tmp_path, "llm_tools")


def test_terminal_reconnect_preserves_queue_without_repeating_start(tmp_path, terminal_factory):
    terminal = terminal_factory(GIGAAM_TEST_WORKER_DELAY_START="1")
    path = tmp_path / "recover.wav"
    path.touch()
    drop(terminal, path)
    queued(terminal, 1)
    terminal.send("\x1b[15~")
    terminal.wait(lambda t: "Запускаем обработку" in t.text)
    old_pid = int((tmp_path / "worker.pid").read_text())
    os.kill(old_pid, signal.SIGKILL)
    terminal.wait(lambda t: "Переподключить" in t.text)
    terminal.send("\x12")  # Ctrl+R
    terminal.wait(lambda t: "Готово" in t.text and int((tmp_path / "worker.pid").read_text()) != old_pid)
    assert "recover.wav" in terminal.queue_text
    assert len(commands(tmp_path, "start")) == 1
