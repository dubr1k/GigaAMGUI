"""Главное окно собрано из mixin'ов: одно имя — одно определение.

Метод с тем же именем в двух mixin'ах молча перекрывает другой по MRO: правка
«не той» копии ничего не меняет, а порядок базовых классов решает, какая
работает. Особенно опасны Qt-переопределения (closeEvent, keyPressEvent,
drag*/dropEvent): второй closeEvent отменил бы подтверждение выхода целиком.
"""

import os
import sys
import types
from collections import defaultdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402

_QT_OVERRIDES = (
    "closeEvent",
    "keyPressEvent",
    "dragEnterEvent",
    "dragLeaveEvent",
    "dropEvent",
    "showEvent",
    "hideEvent",
    "resizeEvent",
    "event",
)


def _gui_classes():
    return [cls for cls in GigaTranscriberQtApp.__mro__ if cls.__module__.startswith("src.gui.")]


def test_mixins_do_not_define_the_same_name_twice():
    owners = defaultdict(list)
    for cls in _gui_classes():
        for name in vars(cls):
            if name.startswith("__") and name.endswith("__"):
                continue
            owners[name].append(cls.__name__)
    duplicates = {name: classes for name, classes in owners.items() if len(classes) > 1}
    assert not duplicates, duplicates


def test_qt_event_overrides_have_a_single_owner():
    for name in _QT_OVERRIDES:
        defined_in = [cls.__name__ for cls in _gui_classes() if name in vars(cls)]
        assert len(defined_in) <= 1, (name, defined_in)
