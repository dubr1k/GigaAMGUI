"""
PyInstaller hook для python-docx (импортируется как ``docx``).

Нужен, потому что ``Document()`` без аргумента открывает вшитый в пакет шаблон
``docx/templates/default.docx`` (и сопутствующие xml-части). Как hidden-import
собирается только код, а без этих data-файлов DOCX-экспорт падает в рантайме.

Хук применяется автоматически всеми спеками (у них ``hookspath=pyinstaller_hooks``),
поэтому docx корректно попадает в сборки на macOS, Windows и Linux.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = collect_all('docx')

# python-docx тянет lxml для разбора OOXML — подстрахуемся подмодулями.
hiddenimports += collect_submodules('docx') + collect_submodules('lxml')
