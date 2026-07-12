"""
Единый контракт полноты сборки для портативных/CUDA спеков.

Проблема: torch/torchaudio/torchvision качаются в рантайме и на этапе заморозки
PyInstaller НЕ видит, что они импортируют. Их чистопитоновые зависимости
(Pillow и т.п.) поэтому собираются частично — отсюда класс ошибок вида
`ImportError: cannot import name 'ImageEnhance' from 'PIL'` (issue #19).

Решение: здесь перечислены пакеты, которые рантайм-torch-семейство импортирует,
и они собираются ЦЕЛИКОМ (collect_all). Любой спек, качающий torch в рантайме,
обязан подмешать результат collect_pure_runtime_deps().

Как чинить будущий `ImportError: cannot import name X from <pkg>` в рантайм-цепочке
импорта: добавь <pkg> в PURE_RUNTIME_DEPS — НЕ перечисляй подмодули вручную в спеке.
"""

from PyInstaller.utils.hooks import collect_all

# Пакеты, которые импортирует рантайм-torchvision/torchaudio, но не видит
# замороженный анализ. Собираем целиком.
PURE_RUNTIME_DEPS = ["PIL"]


def collect_pure_runtime_deps():
    """Возвращает (datas, binaries, hiddenimports) для PURE_RUNTIME_DEPS целиком."""
    datas, binaries, hiddenimports = [], [], []
    for pkg in PURE_RUNTIME_DEPS:
        try:
            d, b, h = collect_all(pkg)
        except Exception as e:  # пакет отсутствует в build-окружении
            print(f"[_spec_common] collect_all({pkg}) failed: {e}")
            continue
        datas += d
        binaries += b
        hiddenimports += h
    return datas, binaries, hiddenimports
