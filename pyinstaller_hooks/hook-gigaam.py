"""
PyInstaller hook для пакета gigaam (установлен как editable из git).
Принудительно собирает весь пакет вместе с данными конфигураций.

Перед сборкой проверяем, что `gigaam` — настоящий пакет. Если в site-packages
лежит каталог `gigaam/` без `__init__.py` (например, только конфликтные копии
Syncthing), Python находит его раньше editable-установки и импортирует как
пустой namespace-пакет: `import gigaam` проходит, а `from gigaam import
load_model` — нет. collect_all() при этом молча собирает пустышку, и
PyTorch-бэкенд падает уже у пользователя. Такой бандл однажды ушёл в
локальную установку Liquid, поэтому сборка обязана упасть здесь.
"""

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


def _require_real_gigaam_package() -> None:
    # Без импорта: gigaam/__init__ тянет torch, а хук выполняется в процессе
    # PyInstaller. Достаточно убедиться, что пакет обычный и объявляет load_model.
    spec = importlib.util.find_spec("gigaam")
    locations = list(getattr(spec, "submodule_search_locations", None) or []) if spec else []
    origin = getattr(spec, "origin", None)
    if spec is None or origin in (None, "namespace") or not Path(origin).is_file():
        raise SystemExit(
            "hook-gigaam: пакет gigaam найден как пустой namespace-пакет "
            f"({', '.join(locations) or 'нет путей'}). Удалите каталог-пустышку "
            "из site-packages и переустановите gigaam."
        )
    if "def load_model(" not in Path(origin).read_text(encoding="utf-8", errors="replace"):
        raise SystemExit(f"hook-gigaam: в {origin} нет gigaam.load_model — пакет повреждён.")


def _without_sync_conflicts(entries):
    return [entry for entry in entries if ".sync-conflict-" not in str(entry[0])]


_require_real_gigaam_package()

datas, binaries, hiddenimports = collect_all('gigaam')
datas = _without_sync_conflicts(datas)
binaries = _without_sync_conflicts(binaries)

# Явно добавляем все подмодули
hiddenimports += collect_submodules('gigaam')
