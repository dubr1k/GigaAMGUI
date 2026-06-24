"""
PyInstaller hook для пакета gigaam (установлен как editable из git).
Принудительно собирает весь пакет вместе с данными конфигураций.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = collect_all('gigaam')

# Явно добавляем все подмодули
hiddenimports += collect_submodules('gigaam')
