# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the macOS GUI bundle.

Builds dist/GigaAMTranscriber.app for Apple Silicon and bundles the installed
PyTorch stack so the desktop GUI can use MPS without a first-run torch download.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

try:
    import numpy as np

    if not hasattr(np, "NaN"):
        np.NaN = np.nan
    if not hasattr(np, "NAN"):
        np.NAN = np.nan
except Exception:
    pass

block_cipher = None

project_root = os.path.abspath(SPECPATH)
icon_icns = os.path.join(project_root, "icon.icns")
icon_file = icon_icns if os.path.exists(icon_icns) else None


def safe_collect(package):
    try:
        return collect_all(package)
    except Exception as exc:
        print(f"[skip] {package}: {exc}")
        return [], [], []


packages = [
    "torch",
    "torchaudio",
    "torchvision",
    "transformers",
    "gigaam",
    "gigaam_mlx",
    "huggingface_hub",
    "mlx",
    "safetensors",
    "tokenizers",
    "einops",
    "omegaconf",
    "accelerate",
    "pyannote.audio",
    "lightning_fabric",
    "pytorch_lightning",
    "speechbrain",
    "librosa",
    "soundfile",
    "onnxruntime",
    "sentencepiece",
    "yt_dlp",
    "docx",
    "dotenv",
    "requests",
    "certifi",
]

datas = []
binaries = []
hiddenimports = []

for package in packages:
    package_datas, package_binaries, package_hiddenimports = safe_collect(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

datas += [
    (os.path.join(project_root, "src"), "src"),
    (os.path.join(project_root, "icon.ico"), "."),
]

bundled_gigaam_dir = os.path.join(project_root, "models", "gigaam")
if os.path.isdir(bundled_gigaam_dir):
    datas.append((bundled_gigaam_dir, "models/gigaam"))

bin_dir = os.path.join(project_root, "bin")
if os.path.isdir(bin_dir):
    datas.append((bin_dir, "bin"))

hiddenimports = sorted(set(hiddenimports + [
    "gigaam",
    "gigaam.load",
    "gigaam_mlx",
    "gigaam_mlx.__main__",
    "torch",
    "torch.backends.mps",
    "torchaudio",
    "torchaudio.functional",
    "torchaudio.transforms",
    "torchvision",
    "transformers",
    "huggingface_hub",
    "soundfile",
    "librosa",
    "scipy",
    "scipy.signal",
    "numpy",
    "PIL",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "dotenv",
    "dotenv.main",
    "yaml",
    "omegaconf",
    "sentencepiece",
    "mlx",
    "onnxruntime",
    "pyannote.audio",
    "speechbrain",
    "yt_dlp",
    "docx",
    "requests",
    "certifi",
    # Ленивый src/gui/__init__ скрывает app_qt от анализа; явный hidden-import
    # заставляет PyInstaller проанализировать его и подтянуть все mixins +
    # core/utils/services штатно (в PYZ), а не только как сырые src/*.py.
    "src.gui.app_qt",
]))

a = Analysis(
    [os.path.join(project_root, "app.py")],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(project_root, "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[os.path.join(project_root, "pyinstaller_hooks", "rthook_utf8.py")],
    excludes=[
        "tkinter",
        "wx",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "coverage",
        "tensorboard",
        "tensorboardX",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GigaAMTranscriber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64" if sys.platform == "darwin" else None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="GigaAMTranscriber",
)

app = BUNDLE(
    coll,
    name="GigaAMTranscriber.app",
    icon=icon_file,
    bundle_identifier="com.dubr1k.gigaamtranscriber",
    info_plist={
        "CFBundleName": "GigaAM Transcriber",
        "CFBundleDisplayName": "GigaAM Transcriber",
        "CFBundleShortVersionString": "1.1.0",
        "CFBundleVersion": "1.1.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "GigaAM Supported Media",
                "CFBundleTypeExtensions": [
                    "mp3", "wav", "m4a", "aac", "flac", "ogg", "mp4",
                    "avi", "mov", "mkv", "webm", "wma", "qta", "3gp",
                ],
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
            },
            {
                "CFBundleTypeName": "GigaAM Transcript Files",
                "CFBundleTypeExtensions": ["txt", "md", "srt", "vtt"],
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
            },
        ],
    },
)
