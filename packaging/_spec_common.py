"""
Единый контракт полноты сборки для портативных/CUDA спеков.

Проблема: torch/torchaudio/torchvision качаются в рантайме и на этапе заморозки
PyInstaller НЕ видит, что они импортируют. Их чистопитоновые зависимости
(Pillow и т.п.) поэтому собираются частично — отсюда класс ошибок вида
`ImportError: cannot import name 'ImageEnhance' from 'PIL'` (issue #19).

Решение: здесь перечислены пакеты, которые рантайм-torch/pyannote-семейство
импортирует, и они собираются ЦЕЛИКОМ (collect_all). Любой спек, качающий torch
в рантайме, обязан подмешать результат collect_pure_runtime_deps().

Как чинить будущий `ImportError: cannot import name X from <pkg>` в рантайм-цепочке
импорта: добавь <pkg> в PURE_RUNTIME_DEPS — НЕ перечисляй подмодули вручную в спеке.
"""

import subprocess
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    get_all_package_paths,
)

# Пакеты, которые импортирует рантайм-torchvision/pyannote, но не видит
# замороженный анализ. Собираем целиком.
PURE_RUNTIME_DEPS = ["PIL", "asteroid_filterbanks"]


#: Куда кладём libportaudio для Linux-сборки. Читается из
#: ``src/live/capture/linux.py`` — менять только вместе с ним.
BUNDLED_PORTAUDIO_RELPATH = "_portaudio/libportaudio.so.2"


def collect_linux_portaudio():
    """Вшивает системный ``libportaudio.so.2`` в Linux-сборку.

    Linux-колесо sounddevice — чистопитоновое: в отличие от macOS/Windows оно НЕ
    содержит ``_sounddevice_data/portaudio-binaries``, а загрузчик ищет библиотеку
    через ``ctypes.util.find_library`` и на Linux не имеет запасного пути. Поэтому
    саму библиотеку кладём в бандл сами, а ``src/live/capture/linux.py`` при старте
    из бандла подставляет sounddevice этот путь (issue #47).
    """
    soname = Path(BUNDLED_PORTAUDIO_RELPATH).name
    candidates = []

    for ldconfig in ("/sbin/ldconfig", "/usr/sbin/ldconfig", "ldconfig"):
        try:
            listing = subprocess.run(
                [ldconfig, "-p"], capture_output=True, text=True, check=False
            ).stdout
        except OSError:
            continue
        for line in listing.splitlines():
            name, _, path = line.strip().partition(" => ")
            if name.split(" ", maxsplit=1)[0] == soname and path:
                candidates.append(path)
        break

    candidates += [
        str(path)
        for pattern in (f"/usr/lib/*/{soname}", f"/usr/lib/{soname}", f"/lib/*/{soname}")
        for path in sorted(Path("/").glob(pattern.lstrip("/")))
    ]

    for candidate in candidates:
        if Path(candidate).is_file():
            return [(candidate, str(Path(BUNDLED_PORTAUDIO_RELPATH).parent))]

    print(f"[skip] {soname} not found on the build host: Linux live capture will need the system package")
    return []


def collect_live_capture_deps():
    """Collect optional native live-capture packages for target platform."""
    if sys.platform.startswith("win"):
        packages = ("pyaudiowpatch",)
    elif sys.platform == "darwin":
        # ``_sounddevice`` — cffi-обвязка, ``_sounddevice_data`` — отдельный
        # top-level пакет с libportaudio.dylib, который collect_all('sounddevice')
        # не видит; без него замороженный импорт sounddevice падает.
        packages = (
            "sounddevice",
            "_sounddevice",
            "_sounddevice_data",
            "AVFoundation",
            "CoreMedia",
            "ScreenCaptureKit",
        )
    elif sys.platform.startswith("linux"):
        packages = ("sounddevice", "_sounddevice")
    else:
        packages = ()

    datas, binaries, hiddenimports = [], [], []
    for package in packages:
        try:
            package_data, package_binaries, package_hiddenimports = collect_all(package)
        except Exception as exc:
            print(f"[skip] optional live capture package {package}: {exc}")
            continue
        datas += package_data
        binaries += package_binaries
        hiddenimports += package_hiddenimports

    if sys.platform.startswith("linux"):
        binaries += collect_linux_portaudio()
    return datas, binaries, hiddenimports


def collect_static_package(package):
    """Собирает пакет и его подмодули без выполнения ``package.__init__``.

    ``collect_all('pyannote.audio')`` вызывает пакет в изолированном процессе.
    pyannote.audio 3.1.1 при NumPy 2.x падает там на удалённом ``np.NaN``, после
    чего PyInstaller молча возвращает пустой hiddenimports. Статический обход
    сохраняет полный граф модулей и позволяет анализатору увидеть их импорты.
    """
    datas = collect_data_files(package, include_py_files=True)
    binaries = collect_dynamic_libs(package)
    hiddenimports = set()

    for package_path in get_all_package_paths(package):
        root = Path(package_path)
        for source in root.rglob("*.py"):
            relative = source.relative_to(root)
            parts = list(relative.parts)
            if parts[-1] == "__init__.py":
                parts.pop()
            else:
                parts[-1] = source.stem
            if any(not part.isidentifier() for part in parts):
                continue
            hiddenimports.add(".".join((package, *parts)) if parts else package)

    if not hiddenimports:
        raise RuntimeError(f"Не найдены Python-модули обязательного пакета {package}")

    return datas, binaries, sorted(hiddenimports)


def collect_pure_runtime_deps():
    """Возвращает (datas, binaries, hiddenimports) для PURE_RUNTIME_DEPS целиком."""
    datas, binaries, hiddenimports = [], [], []
    for pkg in PURE_RUNTIME_DEPS:
        try:
            d, b, h = collect_all(pkg)
        except Exception as e:  # обязательная зависимость отсутствует/сломана
            raise RuntimeError(
                f"Не удалось собрать обязательную runtime-зависимость {pkg}: {e}"
            ) from e
        datas += d
        binaries += b
        hiddenimports += h
    return datas, binaries, hiddenimports


def collect_onnx_runtime_deps():
    """Собирает Python-код, model metadata/data и native-библиотеки ONNX runtime."""
    datas, binaries, hiddenimports = [], [], []
    for package in ("onnx_asr", "onnxruntime"):
        try:
            d, b, h = collect_all(package)
        except Exception as exc:
            raise RuntimeError(
                f"Не удалось собрать обязательную ONNX-зависимость {package}: {exc}"
            ) from exc
        datas += d
        binaries += b
        hiddenimports += h
    return datas, binaries, hiddenimports
