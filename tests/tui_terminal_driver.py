"""Минимальный VT-драйвер используемых crossterm команд; только для тестов."""
import codecs
import os
import re
import select
import signal
import subprocess
import sys
import time
import unicodedata
from pathlib import Path


class TerminalScreen:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.cells = [[" "] * width for _ in range(height)]
        self.x = self.y = 0
        self.pending = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    @property
    def lines(self):
        return ["".join(row) for row in self.cells]

    def resize(self, width, height):
        self.cells = [(row[:width] + [" "] * width)[:width] for row in self.cells[:height]]
        self.cells.extend([[" "] * width for _ in range(height - len(self.cells))])
        self.width, self.height = width, height
        self.x, self.y = min(self.x, width - 1), min(self.y, height - 1)

    def feed(self, data):
        self.pending += self.decoder.decode(data)
        replies = bytearray()
        while self.pending:
            if self.pending.startswith("\x1b"):
                if len(self.pending) < 2:
                    break
                if self.pending[1] in "]_P":
                    end = re.search("\x07|\x1b\\\\", self.pending[2:])
                    if end is None:
                        break
                    self.pending = self.pending[2 + end.end():]
                    continue
                if self.pending[1] == "[":
                    match = re.match(r"\x1b\[([0-?]*)([ -/]*)([@-~])", self.pending)
                    if match is None:
                        break
                    params, _, command = match.groups()
                    if command == "n" and params == "6":
                        replies.extend(b"\x1b[1;1R")
                    elif command == "n" and params == "5":
                        replies.extend(b"\x1b[0n")
                    self._csi(params, command)
                    self.pending = self.pending[match.end():]
                    continue
                self.pending = self.pending[2:]
                continue
            character, self.pending = self.pending[0], self.pending[1:]
            if character == "\r":
                self.x = 0
            elif character == "\n":
                self.y = min(self.y + 1, self.height - 1)
            elif character == "\b":
                self.x = max(0, self.x - 1)
            elif character == "\t":
                self.x = min((self.x // 8 + 1) * 8, self.width - 1)
            elif ord(character) >= 32:
                if unicodedata.combining(character):
                    if self.x:
                        self.cells[self.y][self.x - 1] += character
                    continue
                size = 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
                if self.x < self.width:
                    self.cells[self.y][self.x] = character
                    if size == 2 and self.x + 1 < self.width:
                        self.cells[self.y][self.x + 1] = ""
                self.x = min(self.width, self.x + size)
        return bytes(replies)

    def _csi(self, params, command):
        if params.startswith("?"):
            if params == "?1049" and command == "h":
                self.cells = [[" "] * self.width for _ in range(self.height)]
            return
        numbers = [int(part or 0) for part in params.split(";")] if params else [0]
        n = numbers[0] or 1
        if command in ("H", "f"):
            self.y = min((numbers[0] or 1) - 1, self.height - 1)
            self.x = min(((numbers[1] if len(numbers) > 1 else 1) or 1) - 1, self.width - 1)
        elif command == "A":
            self.y = max(0, self.y - n)
        elif command in ("B", "e"):
            self.y = min(self.height - 1, self.y + n)
        elif command in ("C", "a"):
            self.x = min(self.width - 1, self.x + n)
        elif command == "D":
            self.x = max(0, self.x - n)
        elif command in ("G", "`"):
            self.x = min(self.width - 1, n - 1)
        elif command == "d":
            self.y = min(self.height - 1, n - 1)
        elif command == "J":
            if numbers[0] in (2, 3):
                self.cells = [[" "] * self.width for _ in range(self.height)]
            elif numbers[0] == 0:
                self.cells[self.y][self.x:] = [" "] * (self.width - self.x)
                for row in range(self.y + 1, self.height):
                    self.cells[row] = [" "] * self.width
        elif command == "K":
            first, last = {0: (self.x, self.width), 1: (0, self.x + 1), 2: (0, self.width)}.get(numbers[0], (0, 0))
            self.cells[self.y][first:last] = [" "] * (last - first)
        elif command == "X":
            last = min(self.width, self.x + n)
            self.cells[self.y][self.x:last] = [" "] * (last - self.x)


class TerminalSession:
    def __init__(self, binary, directory, **overrides):
        import fcntl
        import pty
        import struct
        import termios

        self.master, slave = pty.openpty()
        self.initial_termios = termios.tcgetattr(slave)
        self.screen = TerminalScreen(160, 40)
        self.output = bytearray()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 160, 0, 0))

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.directory = Path(directory)
        self.env = {
            **os.environ, "TERM": "xterm-256color",
            "GIGAAM_CONFIG_DIR": str(self.directory / "config"),
            "GIGAAM_PYTHON": sys.executable,
            "GIGAAM_PROJECT_ROOT": str(Path(__file__).resolve().parents[1]),
            "GIGAAM_TUI_WORKER": "tests.fixtures.tui_worker_stub",
            "GIGAAM_TEST_WORKER_DIR": str(self.directory),
            **overrides,
        }
        self.env.pop("GIGAAM_TUI_WORKER_EXE", None)
        try:
            self.process = subprocess.Popen([binary], stdin=slave, stdout=slave, stderr=slave,
                                            env=self.env, preexec_fn=controlling_terminal)
        finally:
            os.close(slave)

    @property
    def text(self):
        return "\n".join(self.screen.lines)

    @property
    def queue_text(self):
        lines = self.screen.lines
        top = next((i for i, line in enumerate(lines) if "Очередь" in line), None)
        bottom = next((i for i, line in enumerate(lines) if "┌ Прогресс" in line), None)
        if top is None or bottom is None:
            return ""
        right = lines[top].find("┐")
        return "\n".join(line[:right] for line in lines[top:bottom])

    def send(self, value):
        os.write(self.master, value.encode() if isinstance(value, str) else value)

    def pump(self, timeout=0.05):
        if select.select([self.master], [], [], timeout)[0]:
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            self.output.extend(data)
            reply = self.screen.feed(data)
            if reply:
                self.send(reply)
            return bool(data)
        return False

    def wait(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            if predicate(self):
                # Заголовок и строки одного кадра могут прийти разными PTY-порциями.
                while self.pump(timeout=0.01) and time.monotonic() < deadline:
                    pass
                if predicate(self):
                    return
        raise AssertionError(self.text + "\n\n" + self.output[-3000:].decode(errors="replace"))

    def paste(self, text):
        self.send("\x1b[200~" + text + "\x1b[201~")

    def command(self, text):
        self.paste(text)
        self.send("\r")

    def resize(self, width, height):
        import fcntl
        import struct
        import termios
        self.screen.resize(width, height)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
        os.kill(self.process.pid, signal.SIGWINCH)

    def click(self, label):
        for y, line in enumerate(self.screen.lines):
            if label in line:
                x = line.index(label)
                self.send(f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m")
                return
        raise AssertionError(f"{label}\n{self.text}")

    def close(self):
        # Убиваем только собственный subprocess; его worker завершится по EOF stdin.
        if self.process.poll() is None:
            self.process.kill()
        # На macOS непрочитанный PTY может держать процесс в write даже после kill.
        os.close(self.master)
        self.process.wait(timeout=5)
