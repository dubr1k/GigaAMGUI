# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec для macOS x86_64 (Intel) — dist/GigaAMTranscriber.app.

Отличие от ``gigaam_app_mac.spec``: сборка идёт БЕЗ torch и без mlx. Под macOS
x86_64 колёса PyTorch закончились на 2.2.2, а проект требует torch>=2.6.0, так
что Intel-мак не запускает ни готовый arm64-ассет, ни установку из исходников
(issue #45). Вместе с torch отпадают pyannote.audio, lightning, speechbrain и
accelerate: распознавание, VAD и диаризация целиком идут через onnx-asr и ONNX
Runtime, где на x86_64 доступны провайдеры CoreML и CPU.

Отсюда же жёсткий список excludes: случайно приехавший torch удвоил бы вес
бандла и притащил бы бинарники не той архитектуры. Гейт на это — в
``scripts/verify_macos_bundle.py --profile x86_64-onnx``.

Сборка: python -m PyInstaller packaging/gigaam_app_mac_x86_64.spec --noconfirm
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, os.path.abspath(SPECPATH))
from _spec_common import APP_BUILD_VERSION, APP_MARKETING_VERSION, APP_VERSION, collect_live_capture_deps, collect_onnx_runtime_deps

# collect_pure_runtime_deps() сюда не подмешивается сознательно: PIL и
# asteroid_filterbanks нужны рантайм-torchvision и pyannote, которых в этой
# сборке нет, а их отсутствие в окружении уронило бы спек на ровном месте.
onnx_d, onnx_b, onnx_h = collect_onnx_runtime_deps()
live_d, live_b, live_h = collect_live_capture_deps()

block_cipher = None

project_root = os.path.dirname(os.path.abspath(SPECPATH))  # spec лежит в packaging/
icon_icns = os.path.join(project_root, "assets", "icon.icns")
icon_file = icon_icns if os.path.exists(icon_icns) else None


def safe_collect(package):
    try:
        return collect_all(package)
    except Exception as exc:
        print(f"[skip] {package}: {exc}")
        return [], [], []


packages = [
    "huggingface_hub",
    "librosa",
    "soundfile",
    "soxr",
    "scipy",
    "onnxruntime",
    "yt_dlp",
    "docx",
    "dotenv",
    "requests",
    "certifi",
]

# Не «на всякий случай», а гарантия: любой из этих пакетов внутри бандла означает
# либо неверную архитектуру бинарников, либо лишний гигабайт веса.
excluded_modules = [
    "torch",
    "torchaudio",
    "torchvision",
    "torchmetrics",
    "mlx",
    "gigaam",
    "gigaam_mlx",
    "pyannote",
    "pyannote.audio",
    "lightning",
    "lightning_fabric",
    "pytorch_lightning",
    "speechbrain",
    "accelerate",
    "transformers",
    "nemo",
    "tkinter",
    "wx",
    "jupyter",
    "notebook",
    "IPython",
    "pytest",
    "coverage",
    "tensorboard",
    "tensorboardX",
]

datas = []
binaries = []
hiddenimports = []

for package in packages:
    package_datas, package_binaries, package_hiddenimports = safe_collect(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

datas += live_d
binaries += live_b
hiddenimports += live_h
datas += onnx_d
binaries += onnx_b
hiddenimports += onnx_h

datas += [
    (os.path.join(project_root, "assets", "icon.ico"), "."),
    (os.path.join(project_root, "licenses", "parakeet-rs-MIT.md"), "licenses"),
]

bin_dir = os.path.join(project_root, "bin")
if os.path.isdir(bin_dir):
    datas.append((bin_dir, "bin"))

hiddenimports = sorted(set(hiddenimports + [
    "huggingface_hub",
    "onnxruntime",
    "onnx_asr",
    "soundfile",
    "soxr",
    "librosa",
    "scipy",
    "scipy.signal",
    "numpy",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "dotenv",
    "dotenv.main",
    "yaml",
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
    target_arch="x86_64",
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
        "CFBundleShortVersionString": APP_MARKETING_VERSION,
        "GigaAMReleaseVersion": APP_VERSION,
        "CFBundleVersion": APP_BUILD_VERSION,
        # Колёса onnxruntime 1.23.2 под macOS x86_64 требуют macOS 13+.
        "LSMinimumSystemVersion": "13.0",
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
