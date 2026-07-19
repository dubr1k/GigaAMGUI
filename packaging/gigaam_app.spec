# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec для GigaAM v3 Transcriber (torch 2.6 CPU)
"""

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

sys.path.insert(0, os.path.abspath(SPECPATH))
from _spec_common import collect_onnx_runtime_deps, collect_pure_runtime_deps, collect_static_package
runtime_d, runtime_b, runtime_h = collect_pure_runtime_deps()
onnx_d, onnx_b, onnx_h = collect_onnx_runtime_deps()

block_cipher = None

def safe_collect(pkg):
    try:
        return collect_all(pkg)
    except Exception as e:
        print(f"[skip] {pkg}: {e}")
        return [], [], []

# Собираем пакеты
torch_d,        torch_b,        torch_h        = safe_collect('torch')
torchaudio_d,   torchaudio_b,   torchaudio_h   = safe_collect('torchaudio')
torchvision_d,  torchvision_b,  torchvision_h  = safe_collect('torchvision')
transformers_d, transformers_b, transformers_h = safe_collect('transformers')
gigaam_d,       gigaam_b,       gigaam_h       = safe_collect('gigaam')
hf_d,           hf_b,           hf_h           = safe_collect('huggingface_hub')
safetensors_d,  safetensors_b,  safetensors_h  = safe_collect('safetensors')
tokenizers_d,   tokenizers_b,   tokenizers_h   = safe_collect('tokenizers')
pyqt6_d,        pyqt6_b,        pyqt6_h        = safe_collect('PyQt6')
einops_d,       einops_b,       einops_h       = safe_collect('einops')
omegaconf_d,    omegaconf_b,    omegaconf_h    = safe_collect('omegaconf')
accelerate_d,   accelerate_b,   accelerate_h   = safe_collect('accelerate')
pyannote_d,     pyannote_b,     pyannote_h     = collect_static_package('pyannote.audio')
lightning_d,    lightning_b,    lightning_h    = safe_collect('lightning_fabric')
ptl_d,          ptl_b,          ptl_h          = safe_collect('pytorch_lightning')

project_root = r'C:\Users\baggr\Desktop\USB_backup\GigaAMGUI'

datas = (
    torch_d + torchaudio_d + torchvision_d +
    transformers_d + gigaam_d + hf_d +
    safetensors_d + tokenizers_d +
    pyqt6_d + einops_d + omegaconf_d + accelerate_d + pyannote_d + lightning_d + ptl_d +
    runtime_d + onnx_d +
    [(os.path.join(project_root, 'src'), 'src'),
     (os.path.join(project_root, 'assets', 'icon.ico'), '.'),
     (os.path.join(project_root, 'bin'), 'bin')]
)

_extra_bins = []
for _dll in ['_lzma.pyd', '_bz2.pyd', '_sqlite3.pyd']:
    _p = os.path.join(r'C:\Users\baggr\miniconda3\envs\gigaam\DLLs', _dll)
    if os.path.exists(_p):
        _extra_bins.append((_p, '.'))
# liblzma.dll отсутствует в gigaam env но нужна для _lzma.pyd
_liblzma = r'C:\Users\baggr\miniconda3\Library\bin\liblzma.dll'
if os.path.exists(_liblzma):
    _extra_bins.append((_liblzma, '.'))

binaries = (
    torch_b + torchaudio_b + torchvision_b +
    transformers_b + gigaam_b + hf_b +
    safetensors_b + tokenizers_b +
    pyqt6_b + einops_b + omegaconf_b + accelerate_b + pyannote_b + lightning_b + ptl_b +
    runtime_b + onnx_b +
    _extra_bins
)

hiddenimports = list(set(
    torch_h + torchaudio_h + torchvision_h +
    transformers_h + gigaam_h + hf_h +
    safetensors_h + tokenizers_h +
    pyqt6_h + einops_h + omegaconf_h + accelerate_h + pyannote_h + lightning_h + ptl_h +
    runtime_h + onnx_h + [
    'gigaam', 'gigaam.load',
    'torch', 'torch.nn', 'torch.nn.functional', 'torch.nn.modules',
    'torch.optim', 'torch.utils', 'torch.utils.data',
    'torch.cuda', 'torch.jit', 'torch.backends', 'torch.backends.cudnn',
    'torch.onnx', 'torch._C',
    'torchaudio', 'torchaudio.transforms', 'torchaudio.functional',
    'torchvision', 'torchvision.transforms',
    'transformers', 'transformers.models', 'transformers.models.auto',
    'transformers.modeling_utils', 'transformers.tokenization_utils_base',
    'huggingface_hub', 'huggingface_hub.file_download',
    'soundfile', 'librosa', 'scipy', 'scipy.signal',
    'audioread', 'numpy',
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
        'tkinter', 'wx',
        'IPython', 'jupyter', 'notebook',
        'pytest', 'coverage',
        'tensorboard', 'tensorboardX',
        'torch.distributed._shard',
        'torch.distributed._sharded_tensor',
        'torch.distributed._sharding_spec',
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
    [],
    exclude_binaries=True,
    name='GigaAMTranscriber',
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
    icon=os.path.join(project_root, 'assets', 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='GigaAMTranscriber',
)
