import importlib.machinery
import io
import os
import sys
import types

try:
    import numpy as np

    if not hasattr(np, 'NaN'):
        np.NaN = np.nan
    if not hasattr(np, 'NAN'):
        np.NAN = np.nan
except Exception:
    pass

# Mock torchcodec для портативной сборки: pyannote.audio пытается импортировать
# torchcodec при загрузке модуля io.py. В портативной сборке torchcodec исключён,
# а аудио загружается через soundfile (см. pyannote_patch.py).
if 'torchcodec' not in sys.modules:
    try:
        import torchcodec  # noqa: F401
    except Exception:
        _mock_tc = types.ModuleType('torchcodec')
        _mock_tc.__version__ = '0.0.0-mock'
        _mock_tc.__path__ = []
        _mock_tc.__spec__ = importlib.machinery.ModuleSpec('torchcodec', None)

        class _MockClass:
            pass

        _mock_decoders = types.ModuleType('torchcodec.decoders')
        _mock_decoders.__spec__ = importlib.machinery.ModuleSpec('torchcodec.decoders', None)
        _mock_decoders.AudioDecoder = _MockClass
        _mock_decoders.AudioStreamMetadata = _MockClass
        _mock_tc.AudioSamples = _MockClass
        _mock_tc.decoders = _mock_decoders
        sys.modules['torchcodec'] = _mock_tc
        sys.modules['torchcodec.decoders'] = _mock_decoders

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
