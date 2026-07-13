"""PyInstaller hook для gigaam_mlx и mlx runtime."""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = collect_all("gigaam_mlx")
hiddenimports += collect_submodules("gigaam_mlx")

# sentencepiece обычно тянется транзитивно, но оставляем явное правило для стабильности.
hiddenimports += ["sentencepiece"]
