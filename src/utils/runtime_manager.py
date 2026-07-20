"""
Менеджер сменных рантаймов PyTorch (CPU / CUDA 12.4 / CUDA 12.8).

Зачем это нужно
---------------
PyTorch распространяется тремя несовместимыми бинарными сборками:

    * CPU              — без CUDA, работает везде;
    * cu124            — CUDA 12.4, GPU NVIDIA RTX 20xx / 30xx / 40xx;
    * cu128            — CUDA 12.8, GPU NVIDIA RTX 50xx (Blackwell).

Одним встроенным `torch` переключаться между ними в рантайме нельзя — CPU-сборка
физически не содержит CUDA. Поэтому приложение НЕ пакует torch внутрь .exe, а при
первом запуске (и при смене устройства в настройках) скачивает нужную сборку в
пользовательский кэш и подставляет её в ``sys.path`` ДО первого ``import torch``.

Скачанные сборки не удаляются: обратное переключение происходит мгновенно, без
повторной загрузки.

Важно: весь модуль не импортирует torch и может безопасно вызываться на самом
раннем этапе старта приложения.
"""

from __future__ import annotations

import gc
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import torch_downloader

# ── Описание доступных вариантов рантайма ─────────────────────────────────────
#
# index          — pip index-url со сборками torch под конкретный ускоритель;
# index          — pip index-url (None = обычный PyPI);
# torch_device   — предпочтительное устройство torch (cuda/cpu/auto);
# label / hint    — тексты для диалога выбора.
#
# Набор вариантов зависит от ОС: на Windows/Linux доступны сборки под CUDA,
# на macOS — единственная сборка с PyPI (Apple Silicon MPS + CPU в одном).
# Версии берутся из официальных совместимых троек PyTorch. Ветка 2.6 сохраняет
# старый torchaudio backend API, а 2.8 — последняя ветка до его удаления в 2.9
# и при этом поддерживает CUDA 12.8 / Blackwell.

_TORCH_26_STACK = {
    "torch": "2.6.0",
    "torchaudio": "2.6.0",
    "torchvision": "0.21.0",
}

_TORCH_28_STACK = {
    "torch": "2.8.0",
    "torchaudio": "2.8.0",
    "torchvision": "0.23.0",
}

_VARIANTS_CUDA: dict[str, dict] = {
    "cpu": {
        "index": "https://download.pytorch.org/whl/cpu",
        "packages": _TORCH_26_STACK,
        "torch_device": "cpu",
        "label": "CPU (без видеокарты)",
        "hint": "Работает на любом ПК. Медленнее, но не требует GPU NVIDIA.",
        "size_hint": "~250 МБ",
    },
    "cu124": {
        "index": "https://download.pytorch.org/whl/cu124",
        "packages": _TORCH_26_STACK,
        "torch_device": "cuda",
        "label": "GPU NVIDIA — RTX 20xx / 30xx / 40xx",
        "hint": "CUDA 12.4. Ускорение на видеокартах Turing / Ampere / Ada.",
        "size_hint": "~2.7 ГБ",
    },
    "cu128": {
        "index": "https://download.pytorch.org/whl/cu128",
        "packages": _TORCH_28_STACK,
        "torch_device": "cuda",
        "label": "GPU NVIDIA — RTX 50xx (Blackwell)",
        "hint": "CUDA 12.8. Нужен для новых видеокарт RTX 50xx.",
        "size_hint": "~2.9 ГБ",
    },
}

_VARIANTS_MAC: dict[str, dict] = {
    "default": {
        "index": "https://pypi.org/simple",  # обычный PyPI — сборка с поддержкой Apple Silicon (MPS)
        "packages": _TORCH_26_STACK,
        "torch_device": "auto",
        "label": "Apple Silicon (GPU/MPS) и CPU",
        "hint": "Единая сборка для Mac: ускорение на Apple Silicon (MPS), иначе CPU.",
        "size_hint": "~200 МБ",
    },
}


def _platform_variants() -> dict[str, dict]:
    return _VARIANTS_MAC if sys.platform == "darwin" else _VARIANTS_CUDA


VARIANTS: dict[str, dict] = _platform_variants()

DEFAULT_VARIANT = "default" if sys.platform == "darwin" else "cpu"

# Маркер успешной установки внутри папки варианта.
_OK_MARKER = ".installed_ok"
_DLL_HANDLES: list[object] = []
_RUNTIME_MODULE_PREFIXES = (
    "torch",
    "torchaudio",
    "torchvision",
    "torchcodec",
    "gigaam",
    "pyannote",
)


def _has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яА-ЯёЁ]", text))


def base_dir() -> Path:
    """
    Корневая папка кэша приложения. Внутри — рантаймы torch, кэш моделей
    HuggingFace и файл выбранного устройства.

    * Windows — ``C:\\GigaAMGUICash`` (путь без кириллицы: кириллица ломает
      загрузку нативных DLL и кэш HuggingFace);
    * macOS   — ``~/Library/Caches/GigaAMGUICash``;
    * Linux   — ``$XDG_CACHE_HOME/GigaAMGUICash`` или ``~/.cache/GigaAMGUICash``.

    Переопределяется переменной окружения GIGAAM_RUNTIME_DIR (для тестов).
    """
    override = os.environ.get("GIGAAM_RUNTIME_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path("C:/GigaAMGUICash")
    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Library/Caches/GigaAMGUICash"))
    xdg = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(xdg) / "GigaAMGUICash"


def hf_cache_dir() -> Path:
    """Папка кэша моделей HuggingFace внутри общего кэша приложения."""
    return base_dir() / "hf"


def bundled_hf_cache_dir(frozen: bool | None = None) -> Path | None:
    """Кэш моделей, положенный рядом со сборкой в офлайн-варианте релиза.

    Офлайн-сборка везёт модели папкой ``models/hf`` рядом с исполняемым файлом,
    а не внутри него: в onefile-бинарнике их пришлось бы распаковывать во
    временный каталог при каждом запуске. Папка считается read-only: недостающие
    модели всегда докачиваются в пользовательский кэш приложения.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return None

    executable = Path(sys.executable).resolve()
    candidates = [executable.parent]
    if sys.platform == "darwin":
        parents = executable.parents
        if len(parents) > 3 and parents[2].name.endswith(".app"):
            # Рядом с самим .app и внутри Contents/Resources.
            candidates.extend((parents[3], parents[1] / "Resources"))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))

    for candidate in candidates:
        hub = candidate / "models" / "hf" / "hub"
        if hub.is_dir() and any(hub.iterdir()):
            return hub.parent
    return None


def _runtimes_root() -> Path:
    return base_dir() / "torch"


def variant_dir(variant: str) -> Path:
    """Папка, куда устанавливается конкретный вариант torch."""
    return _runtimes_root() / variant


def _config_path() -> Path:
    return base_dir() / "runtime.json"


def ensure_data_dir() -> None:
    """Создаёт папки кэша (идемпотентно)."""
    _runtimes_root().mkdir(parents=True, exist_ok=True)


# ── Хранение выбранного устройства ────────────────────────────────────────────
# Отдельно от user_settings.json: выбор нужен ДО запуска Qt/torch, а user_settings
# лежит в рабочей папке и завязан на GUI.


def _read_config() -> dict:
    try:
        with open(_config_path(), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_config(cfg: dict) -> None:
    ensure_data_dir()
    tmp = _config_path().with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _config_path())


def get_selected_variant() -> str | None:
    """Возвращает выбранный вариант ('cpu'/'cu124'/'cu128') или None, если не выбран."""
    v = _read_config().get("variant")
    return v if v in VARIANTS else None


def set_selected_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"Неизвестный вариант рантайма: {variant}")
    cfg = _read_config()
    cfg["variant"] = variant
    _write_config(cfg)


def torch_device_for(variant: str | None) -> str:
    """Строка устройства torch для выбранного варианта."""
    if variant and variant in VARIANTS:
        return VARIANTS[variant]["torch_device"]
    return "cpu"


# ── Проверка установленных вариантов ──────────────────────────────────────────


def is_installed(variant: str) -> bool:
    """True, если маркер есть и на диске лежит ожидаемый согласованный стек."""
    if variant not in VARIANTS:
        return False
    target = variant_dir(variant)
    return (target / _OK_MARKER).exists() and _runtime_stack_matches(variant, target)


def _installed_package_version(target: Path, package: str) -> str | None:
    """Читает базовую версию из wheel dist-info без импорта torch/DLL."""
    normalized = package.replace("-", "_")
    prefix = f"{normalized}-"
    versions = set()
    for metadata in target.glob(f"{prefix}*.dist-info"):
        name = metadata.name.removesuffix(".dist-info")
        versions.add(name[len(prefix) :].split("+", 1)[0])
    return versions.pop() if len(versions) == 1 else None


def _runtime_stack_matches(variant: str, target: Path | None = None) -> bool:
    """Проверяет полноту и версии runtime без импорта бинарных пакетов."""
    if variant not in VARIANTS:
        return False
    target = target or variant_dir(variant)
    expected = VARIANTS[variant]["packages"]
    # CUDA Linux wheels rely on separately installed nvidia-* wheels. A stale
    # marker without libcudart used to make the first-run installer skip them.
    if sys.platform.startswith("linux") and variant.startswith("cu"):
        # Test fixtures and legacy CPU-like mock runtimes have no torch metadata.
        # Real CUDA wheels declare nvidia-* dependencies, including cuda-runtime.
        metadata = next(target.glob("torch-*.dist-info/METADATA"), None)
        requires_cuda_runtime = metadata is not None and "nvidia-cuda-runtime" in metadata.read_text(
            encoding="utf-8", errors="ignore"
        )
        if requires_cuda_runtime and not any((target / "nvidia").glob("cuda_runtime/lib/libcudart.so*")):
            return False
    for package, version in expected.items():
        package_dir = target / package.replace("-", "_") / "__init__.py"
        if not package_dir.exists():
            return False
        if _installed_package_version(target, package) != version:
            return False
    return True


def installed_variants() -> list[str]:
    return [v for v in VARIANTS if is_installed(v)]


# ── Активация выбранного рантайма (вызывать ДО import torch) ───────────────────


def _close_dll_handles() -> None:
    while _DLL_HANDLES:
        handle = _DLL_HANDLES.pop()
        try:
            close = getattr(handle, "close", None)
            if close is not None:
                close()
        except OSError:
            pass


def deactivate() -> None:
    """Убирает из sys.path все torch-рантаймы приложения и закрывает DLL handles."""
    root = _runtimes_root()
    sys.path[:] = [entry for entry in sys.path if not entry or not Path(entry).resolve().is_relative_to(root.resolve())]
    _close_dll_handles()
    os.environ.pop("GIGAAM_ACTIVE_VARIANT", None)


def _module_belongs_to_runtime(module) -> bool:
    try:
        # vars() avoids triggering lazy module proxies through __getattr__.
        module_file = vars(module).get("__file__")
    except TypeError:
        return False
    if not module_file:
        return False
    try:
        return Path(module_file).resolve().is_relative_to(_runtimes_root().resolve())
    except (OSError, TypeError):
        return False


def purge_runtime_modules(log_cb=None) -> int:
    """Удаляет из sys.modules torch/gigaam/pyannote-модули текущего рантайма."""
    removed = []
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        if name.startswith(_RUNTIME_MODULE_PREFIXES) or _module_belongs_to_runtime(module):
            removed.append(name)
            sys.modules.pop(name, None)
    importlib.invalidate_caches()
    gc.collect()
    if log_cb and removed:
        log_cb(f"Сброшено модулей рантайма: {len(removed)}")
    return len(removed)


def activate(variant: str) -> None:
    """
    Подставляет папку варианта в начало sys.path и регистрирует пути к DLL,
    чтобы последующий ``import torch`` подхватил именно эту сборку.
    """
    target = variant_dir(variant)
    target_str = str(target)
    if target_str in sys.path:
        sys.path.remove(target_str)
    sys.path.insert(0, target_str)

    # Frozen Linux executables do not inherit the wheel's normal loader paths.
    # Preload libtorch's global dependencies before importing torch.
    if sys.platform.startswith("linux") and target.exists():
        torch_lib = target / "torch" / "lib"
        global_deps = torch_lib / "libtorch_global_deps.so"
        if global_deps.is_file():
            try:
                import ctypes

                ctypes.CDLL(str(global_deps), mode=ctypes.RTLD_GLOBAL)
            except OSError as exc:
                raise RuntimeError(f"Cannot load PyTorch runtime dependencies: {exc}") from exc

    # Регистрируем каталоги с нативными DLL (torch и nvidia-*),
    # чтобы Windows нашла cudnn/cublas/cuda-runtime.
    if hasattr(os, "add_dll_directory") and target.exists():
        dll_dirs = [target / "torch" / "lib"]
        nvidia_root = target / "nvidia"
        if nvidia_root.is_dir():
            dll_dirs.extend(p / "bin" for p in nvidia_root.iterdir() if p.is_dir())
        for d in dll_dirs:
            if d.is_dir():
                try:
                    handle = os.add_dll_directory(str(d))
                    if handle is not None:
                        _DLL_HANDLES.append(handle)
                except OSError:
                    pass

    os.environ["GIGAAM_ACTIVE_VARIANT"] = variant


def switch_runtime(variant: str, log_cb=None) -> bool:
    """Горячо переключает активный torch runtime для следующих import-ов."""
    if variant not in VARIANTS:
        raise ValueError(f"Неизвестный вариант рантайма: {variant}")
    if not is_installed(variant):
        raise RuntimeError(f"Вариант {variant} ещё не установлен")

    if log_cb:
        log_cb(f"Переключение рантайма на {VARIANTS[variant]['label']}…")
    deactivate()
    purge_runtime_modules(log_cb=log_cb)
    activate(variant)
    set_selected_variant(variant)
    if log_cb:
        log_cb("Активирован новый рантайм PyTorch.")
    return True


# ── Установка варианта (прямая загрузка колёс без pip) ────────────────────────


def install_variant(variant: str, log_cb=None, cancel_event=None) -> bool:
    """
    Скачивает и устанавливает выбранный вариант torch в его папку.

    Использует прямую загрузку wheel-файлов (zip-архивов) без pip — это
    устраняет класс ошибок distlib/vendored-pip в PyInstaller onefile-сборках.
    Ранее установленные варианты НЕ трогаются. Прогресс/лог отдаётся в log_cb.
    Возвращает True при успехе.
    """
    if variant not in VARIANTS:
        raise ValueError(f"Неизвестный вариант рантайма: {variant}")

    supported_python = {(3, 10), (3, 11), (3, 12), (3, 13)}
    current_python = (sys.version_info.major, sys.version_info.minor)
    if current_python not in supported_python:

        def _emit_unsupported(line: str) -> None:
            if log_cb:
                log_cb(line)

        _emit_unsupported(
            f"ОШИБКА: текущая версия Python {sys.version_info.major}.{sys.version_info.minor} не поддерживается колёсами PyTorch для этого приложения."
        )
        _emit_unsupported("Используйте Python 3.10–3.13. Рекомендуется Python 3.12.")
        return False

    ensure_data_dir()
    target = variant_dir(variant)
    staging = target.with_name(f".{target.name}.installing")
    backup = target.with_name(f".{target.name}.previous")

    # Восстанавливаем runtime после прерывания между двумя атомарными rename.
    if backup.exists() and not target.exists():
        os.replace(backup, target)
    elif backup.exists():
        shutil.rmtree(backup)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    def _emit(line: str) -> None:
        if log_cb:
            log_cb(line.rstrip() if line else line)

    info = VARIANTS[variant]
    _emit(f"Установка PyTorch ({info['label']})…")
    _emit("Стек: " + ", ".join(f"{package} {version}" for package, version in info["packages"].items()))
    _emit(f"Индекс: {info['index']}")
    _emit(f"Папка: {target}")
    _emit("Это может занять несколько минут и потребует интернет.")

    # На Linux с CUDA дополнительно нужны nvidia-* пакеты (на Windows/macOS
    # они уже вшиты в само колесо torch).
    need_nvidia = sys.platform == "linux" and variant.startswith("cu")

    try:
        torch_downloader.install(
            base_index=info["index"],
            target=staging,
            versions=info["packages"],
            need_nvidia=need_nvidia,
            log_cb=_emit,
            cancel_event=cancel_event,
        )
        if not _runtime_stack_matches(variant, staging):
            raise RuntimeError("после установки версии runtime-пакетов не совпадают")

        marker_payload = {
            "schema": 1,
            "packages": info["packages"],
        }
        (staging / _OK_MARKER).write_text(
            json.dumps(marker_payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except Exception:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except torch_downloader.DownloadCancelled as e:
        _emit(str(e))
        return False
    except Exception as e:
        _emit(f"ОШИБКА загрузки: {e}")
        return False
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    _emit("Готово: PyTorch установлен.")
    return True


# ── Автоопределение GPU для подсказки в диалоге ───────────────────────────────


def detect_recommended_variant() -> str:
    """
    Пытается определить видеокарту и рекомендует вариант.

    RTX 50xx -> cu128; прочие NVIDIA -> cu124; иначе -> cpu.
    На macOS вариант всегда один ('default').
    Работает по best-effort: при любой ошибке возвращает 'cpu'.
    """
    if sys.platform == "darwin":
        return "default"
    name = _detect_gpu_name()
    if not name:
        return "cpu"
    upper = name.upper()
    # RTX 50xx: ищем "50" сразу после RTX (5060/5070/5080/5090).
    if re.search(r"RTX\s*50\d0", upper) or re.search(r"RTX\s*5[0-9]{3}", upper):
        return "cu128"
    if "NVIDIA" in upper or "GEFORCE" in upper or "RTX" in upper or "QUADRO" in upper or "TESLA" in upper:
        return "cu124"
    return "cpu"


def _detect_gpu_name() -> str | None:
    """Возвращает название первой NVIDIA-видеокарты или None."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

    # 1) nvidia-smi — самый надёжный источник.
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=flags,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass

    # 2) Windows: PowerShell/CIM запрос видеоадаптеров.
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=flags,
            )
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    if "NVIDIA" in line.upper() or "RTX" in line.upper() or "GEFORCE" in line.upper():
                        return line.strip()
        except (OSError, subprocess.SubprocessError):
            pass

    return None
