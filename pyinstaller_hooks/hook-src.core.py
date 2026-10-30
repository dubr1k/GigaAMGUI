"""Collect PEP 562 exports without eagerly loading the runtime's ML modules."""

from importlib.util import resolve_name

from PyInstaller.utils.hooks import get_module_attribute

hiddenimports = sorted({
    resolve_name(module, "src.core")
    for module, _attribute in get_module_attribute("src.core", "_LAZY").values()
})
