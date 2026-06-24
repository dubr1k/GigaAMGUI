import sys
import io
import os

# Добавляем bundled bin/ в PATH чтобы gigaam и другие либы находили ffmpeg
if getattr(sys, '_MEIPASS', None):
    _bin = os.path.join(sys._MEIPASS, 'bin')
    if os.path.isdir(_bin):
        os.environ['PATH'] = _bin + os.pathsep + os.environ.get('PATH', '')

# В windowed EXE (без консоли) stdout/stderr — невалидные дескрипторы.
# Перенаправляем их в null чтобы print() не падал с OSError/UnicodeEncodeError.
def _safe_stream(fd):
    try:
        f = io.FileIO(fd, "w")
        f.write(b"")  # проверяем что дескриптор рабочий
        return io.TextIOWrapper(f, encoding="utf-8", errors="replace")
    except OSError:
        return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace")

sys.stdout = _safe_stream(1)
sys.stderr = _safe_stream(2)
