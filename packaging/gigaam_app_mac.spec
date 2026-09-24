# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the macOS GUI and the headless Liquid worker.

The default builds dist/GigaAMTranscriber.app with PyQt. GIGAAM_NATIVE_WORKER=1
builds dist/GigaAMWorker.app without Qt from the same model and media stack.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, os.path.abspath(SPECPATH))
from _spec_common import APP_BUILD_VERSION, APP_MARKETING_VERSION, APP_VERSION, collect_live_capture_deps, collect_onnx_runtime_deps, collect_pure_runtime_deps, collect_static_package

runtime_d, runtime_b, runtime_h = collect_pure_runtime_deps()
onnx_d, onnx_b, onnx_h = collect_onnx_runtime_deps()
live_d, live_b, live_h = collect_live_capture_deps()

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
worker_only = os.environ.get("GIGAAM_NATIVE_WORKER", "").strip().lower() in {
    "1", "true", "yes", "on",
}
executable_name = "GigaAMWorker" if worker_only else "GigaAMTranscriber"

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
if worker_only:
    excluded_modules += ["PyQt6", "PySide6", "src.gui"]


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
datas += live_d
binaries += live_b
hiddenimports += live_h
datas += onnx_d
binaries += onnx_b
hiddenimports += onnx_h

if not worker_only:
    datas.append((os.path.join(project_root, "assets", "icon.ico"), "."))
datas.append((os.path.join(project_root, "licenses", "parakeet-rs-MIT.md"), "licenses"))

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
    *([] if worker_only else ["PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets"]),
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
    # Lazy GUI imports are only relevant to the classic desktop app.
    *([] if worker_only else ["src.gui.app_qt"]),
]))

a = Analysis(
    [os.path.join(project_root, "native_worker.py" if worker_only else "app.py")],
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
    name=executable_name,
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
    icon=None if worker_only else icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=executable_name,
)

app = BUNDLE(
    coll,
    name=executable_name + ".app",
    icon=None if worker_only else icon_file,
    bundle_identifier="com.dubr1k.gigaamworker" if worker_only else "com.dubr1k.gigaamtranscriber",
    info_plist={
        "CFBundleName": executable_name,
        "CFBundleDisplayName": executable_name,
        "CFBundleShortVersionString": APP_MARKETING_VERSION,
        "GigaAMReleaseVersion": APP_VERSION,
        "CFBundleVersion": APP_BUILD_VERSION,
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        **({"LSUIElement": True} if worker_only else {
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
        }),
    },
)
