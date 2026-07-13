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
