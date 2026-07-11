# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — GigaAM v3 Transcriber (ПОРТАТИВНАЯ onefile-сборка).

Отличие от обычных spec-файлов: torch / torchaudio / torchvision НЕ пакуются
внутрь .exe. Нужная сборка PyTorch (CPU / CUDA 12.4 / CUDA 12.8) скачивается при
первом запуске в C:\\GigaAMGUICash\\torch\\<вариант> через прямую загрузку wheel
(urllib + zipfile, без pip) и подставляется в sys.path до import torch.

Сборка:  pyinstaller gigaam_app_portable.spec --noconfirm
Результат: dist/GigaAMTranscriber_portable.exe  (один файл)
"""

import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Корень проекта — папка со spec-файлом (портативно, без хардкода путей).
project_root = os.path.abspath(SPECPATH)

# Иконка .ico — только для Windows; на macOS нужен .icns, Linux игнорирует.
_icon = os.path.join(project_root, 'icon.ico') if sys.platform == 'win32' else None


def safe_collect(pkg):
    try:
        return collect_all(pkg)
    except Exception as e:
        print(f"[skip] {pkg}: {e}")
        return [], [], []


# Собираем пакеты БЕЗ torch/torchaudio/torchvision — они ставятся при первом запуске.
transformers_d, transformers_b, transformers_h = safe_collect('transformers')
gigaam_d,       gigaam_b,       gigaam_h       = safe_collect('gigaam')
hf_d,           hf_b,           hf_h           = safe_collect('huggingface_hub')
safetensors_d,  safetensors_b,  safetensors_h  = safe_collect('safetensors')
tokenizers_d,   tokenizers_b,   tokenizers_h   = safe_collect('tokenizers')
pyqt6_d,        pyqt6_b,        pyqt6_h        = safe_collect('PyQt6')
einops_d,       einops_b,       einops_h       = safe_collect('einops')
omegaconf_d,    omegaconf_b,    omegaconf_h    = safe_collect('omegaconf')
accelerate_d,   accelerate_b,   accelerate_h   = safe_collect('accelerate')
pyannote_d,     pyannote_b,     pyannote_h     = safe_collect('pyannote.audio')
lightning_d,    lightning_b,    lightning_h    = safe_collect('lightning_fabric')
ptl_d,          ptl_b,          ptl_h          = safe_collect('pytorch_lightning')

datas = list(
    transformers_d + gigaam_d + hf_d +
    safetensors_d + tokenizers_d +
    pyqt6_d + einops_d + omegaconf_d + accelerate_d + pyannote_d + lightning_d + ptl_d +
    [(os.path.join(project_root, 'src'), 'src'),
     (os.path.join(project_root, 'icon.ico'), '.')]
)

# Добавляем только ffmpeg/ffprobe, совместимые с текущей ОС сборки.
_bin_dir = os.path.join(project_root, 'bin')
if os.path.isdir(_bin_dir):
    _tool_names = ['ffmpeg.exe', 'ffprobe.exe'] if sys.platform == 'win32' else ['ffmpeg', 'ffprobe']
    for _tool_name in _tool_names:
        _tool_path = os.path.join(_bin_dir, _tool_name)
        if os.path.isfile(_tool_path):
            datas.append((_tool_path, 'bin'))

# Вспомогательные DLL/pyd (_lzma, _bz2, _sqlite3, liblzma) ищем в текущем окружении
# сборки (best-effort, чтобы spec был переносимым между машинами).
_extra_bins = []
_dll_search = [
    os.path.join(sys.base_prefix, 'DLLs'),
    os.path.join(sys.prefix, 'DLLs'),
]
for _dll in ['_lzma.pyd', '_bz2.pyd', '_sqlite3.pyd']:
    for _d in _dll_search:
        _p = os.path.join(_d, _dll)
        if os.path.exists(_p):
            _extra_bins.append((_p, '.'))
            break
for _libdir in [os.path.join(sys.base_prefix, 'Library', 'bin'),
                os.path.join(sys.prefix, 'Library', 'bin')]:
    _liblzma = os.path.join(_libdir, 'liblzma.dll')
    if os.path.exists(_liblzma):
        _extra_bins.append((_liblzma, '.'))
        break

binaries = (
    transformers_b + gigaam_b + hf_b +
    safetensors_b + tokenizers_b +
    pyqt6_b + einops_b + omegaconf_b + accelerate_b + pyannote_b + lightning_b + ptl_b +
    _extra_bins
)

hiddenimports = list(set(
    transformers_h + gigaam_h + hf_h +
    safetensors_h + tokenizers_h +
    pyqt6_h + einops_h + omegaconf_h + accelerate_h + pyannote_h + lightning_h + ptl_h + [
    'gigaam',
    'transformers', 'transformers.models', 'transformers.models.auto',
    'transformers.modeling_utils', 'transformers.tokenization_utils_base',
    'huggingface_hub', 'huggingface_hub.file_download',
    'soundfile', 'librosa', 'scipy', 'scipy.signal',
    'audioread', 'numpy', 'PIL', 'PIL.Image',
    'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.sip',
    'requests', 'urllib3', 'certifi',
    'tqdm', 'packaging', 'filelock', 'fsspec', 'psutil',
    'dotenv', 'dotenv.main',
    'docx', 'yt_dlp',
    # Ленивый src/gui/__init__ скрывает app_qt от анализа; явный hidden-import
    # заставляет PyInstaller проанализировать его и подтянуть все mixins +
    # core/utils/services штатно (в PYZ), а не только как сырые src/*.py.
    'src.gui.app_qt',
    'colorlog', 'colorama',
    'yaml', 'omegaconf',
    'einops', 'sentencepiece',
    'onnxruntime', 'onnx',
    'regex', 'safetensors',
    'accelerate', 'tokenizers',
    'lzma', '_lzma', 'backports.lzma',
    'bz2', '_bz2', 'zlib',
    # Runtime-зависимости torch (качаются в кэш, но некоторые модули torch
    # импортируют их lazy — PyInstaller должен знать о них заранее).
    'sympy', 'mpmath', 'networkx', 'jinja2', 'markupsafe',
    'modulefinder',
]
))

a = Analysis(
    [os.path.join(project_root, 'app.py')],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(project_root, 'pyinstaller_hooks')],
    hooksconfig={},
    runtime_hooks=[os.path.join(project_root, 'pyinstaller_hooks', 'rthook_utf8.py')],
    excludes=[
        # torch-сборки НЕ пакуем — ставятся при первом запуске.
        'torch', 'torchaudio', 'torchvision', 'torchcodec',
        'nvidia',
        'tkinter', 'wx',
        'IPython', 'jupyter', 'notebook',
        'pytest', 'coverage',
        'tensorboard', 'tensorboardX',
        'speechbrain',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    exclude_binaries=False,
    name='GigaAMTranscriber_portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)
