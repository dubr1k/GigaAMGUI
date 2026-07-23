# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the macOS GUI bundle.

Builds dist/GigaAMTranscriber.app for Apple Silicon and bundles the installed
PyTorch stack so the desktop GUI can use MPS without a first-run torch download.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, os.path.abspath(SPECPATH))
from _spec_common import collect_onnx_runtime_deps, collect_pure_runtime_deps, collect_static_package

runtime_d, runtime_b, runtime_h = collect_pure_runtime_deps()
onnx_d, onnx_b, onnx_h = collect_onnx_runtime_deps()

block_cipher = None

project_root = os.path.dirname(os.path.abspath(SPECPATH))  # spec лежит в packaging/, корень проекта — на уровень выше
icon_icns = os.path.join(project_root, "assets", "icon.icns")
icon_file = icon_icns if os.path.exists(icon_icns) else None


def safe_collect(package):
    try:
        return collect_all(package)
    except Exception as exc:
        print(f"[skip] {package}: {exc}")
        return [], [], []


bundle_sortformer = os.environ.get("GIGAAM_BUNDLE_SORTFORMER", "").strip().lower() in {
    "1", "true", "yes", "on",
}

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
if bundle_sortformer:
    packages += [
        "nemo.collections.asr",
        "nemo.collections.common",
        "nemo.core",
        "nemo.utils",
        "lhotse",
    ]

excluded_modules = [
    "tkinter",
    "wx",
    "jupyter",
    "notebook",
    "pytest",
    "coverage",
    "tensorboard",
    "tensorboardX",
]
if not bundle_sortformer:
    excluded_modules.append("IPython")

datas = []
binaries = []
hiddenimports = []

for package in packages:
    collector = collect_static_package if package == "pyannote.audio" else safe_collect
    package_datas, package_binaries, package_hiddenimports = collector(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

datas += runtime_d
binaries += runtime_b
hiddenimports += runtime_h
datas += onnx_d
binaries += onnx_b
hiddenimports += onnx_h

datas += [
    (os.path.join(project_root, "src"), "src"),
    (os.path.join(project_root, "assets", "icon.ico"), "."),
    (os.path.join(project_root, "licenses", "parakeet-rs-MIT.md"), "licenses"),
]

bundled_gigaam_dir = os.path.join(project_root, "models", "gigaam")
bundle_models = os.environ.get("GIGAAM_BUNDLE_MODELS", "").strip().lower() in {
    "1", "true", "yes", "on",
}
if bundle_models and not os.path.isdir(bundled_gigaam_dir):
    raise RuntimeError(
        "GIGAAM_BUNDLE_MODELS включён, но локальная папка models/gigaam отсутствует"
    )
if bundle_models:
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
    excludes=excluded_modules,
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
        "CFBundleShortVersionString": "1.3.6",
        "CFBundleVersion": "1.3.6",
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
