"""Тесты не пишут в настоящий профиль пользователя.

Логгер, статистика обработки и пользовательские настройки по умолчанию
живут в user_config_dir() (на macOS — ~/Library/Application Support/
GigaAMTranscriber). Каждый импорт, поднимавший логгер, создавал там новую
папку сессии логов — сотни за день прогонов. Каталог задаётся здесь, до
импорта тестовых модулей; тесты, которым нужен путь по умолчанию или свой
каталог, по-прежнему снимают или переопределяют переменную сами.
"""

import os
import tempfile

_SESSION_CONFIG_DIR = tempfile.mkdtemp(prefix="gigaam-tests-config-")
os.environ.setdefault("GIGAAM_CONFIG_DIR", _SESSION_CONFIG_DIR)
