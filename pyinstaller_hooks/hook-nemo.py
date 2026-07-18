"""PyInstaller hook for NeMo modules compiled by TorchScript at import time.

NeMo decorates several helpers with ``torch.jit.script``. TorchScript resolves
their source through ``inspect``, so bytecode in PYZ alone is not sufficient.
Keep the regular archive and place matching ``.py`` files next to it.
"""

module_collection_mode = {"nemo": "pyz+py"}
