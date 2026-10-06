"""Тесты экранного PTY-драйвера: история потока не считается текущим экраном."""
from tests.tui_terminal_driver import TerminalScreen


def test_screen_overwrite_erase_cursor_and_split_utf8():
    screen = TerminalScreen(12, 3)
    encoded = "Запись".encode()
    screen.feed(encoded[:3])
    screen.feed(encoded[3:])
    assert screen.lines[0].startswith("Запись")
    screen.feed(b"\x1b[1;1Hnew\x1b[K")
    assert screen.lines[0] == "new         "
    screen.feed(b"\x1b[2;3H123\x1b[2D!\x1b[1X")
    assert screen.lines[1] == "  1!        "
    screen.feed(b"\x1b[2J")
    assert all(not line.strip() for line in screen.lines)


def test_screen_resize_and_wide_cells():
    screen = TerminalScreen(12, 3)
    screen.feed("🦄x\r\nвторая".encode())
    assert screen.lines[0].startswith("🦄x")
    assert screen.x == 6 and screen.y == 1
    screen.resize(8, 2)
    assert len(screen.cells) == 2
    assert len(screen.cells[0]) == 8
    screen.feed(b"\x1b[H\x1b[?25l\x1b[31mOK\x1b[0m")
    assert screen.lines[0].startswith("OKx")


def test_screen_ignores_osc_and_handles_chunked_escape_sequences():
    screen = TerminalScreen(20, 2)
    screen.feed(b"\x1b]0;title")
    screen.feed(b"\x07abc\x1b[")
    screen.feed(b"1;1HXYZ")
    assert screen.lines[0].startswith("XYZ")
    assert "title" not in "\n".join(screen.lines)
