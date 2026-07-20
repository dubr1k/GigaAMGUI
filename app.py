"""
Точка входа GUI приложения GigaAM v3 Transcriber (PyQt6).

Порядок запуска важен: сначала выбирается и активируется нужная сборка PyTorch
(CPU / CUDA 12.4 / CUDA 12.8), и только ПОСЛЕ этого импортируются модули,
которые тянут torch. Иначе torch загрузился бы раньше подстановки sys.path.
"""

import json
import os
import platform
import sys
import time
import warnings
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - macOS build uses fcntl
    fcntl = None


# Проверка целостности сборки: импортирует всю ML-цепочку и выходит 0/1.
# Должна идти ДО любого импорта, тянущего torch (активация варианта — внутри).
if "--selfcheck" in sys.argv:
    from src.selfcheck import run_selfcheck

    raise SystemExit(run_selfcheck())


from src.config import ASR_BACKEND, HF_TOKEN, ONNX_PROVIDER


def _user_config_dir() -> Path:
    override = os.environ.get("GIGAAM_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GigaAMTranscriber"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "GigaAMTranscriber"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "GigaAMTranscriber"


def _argv_open_paths(argv: list[str]) -> list[str]:
    paths = []
    for arg in argv[1:]:
        if arg.startswith("-psn_"):
            continue
        path = os.path.abspath(os.path.expanduser(arg))
        if os.path.exists(path):
            paths.append(path)
    return paths


def _try_acquire_instance_lock():
    lock_path = _user_config_dir() / "instance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w", encoding="utf-8")
    if fcntl is None:
        return lock_file
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _queue_open_request(paths: list[str]) -> None:
    queue_path = _user_config_dir() / "open_requests.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"paths": paths, "pid": os.getpid(), "time": time.time()}
    with queue_path.open("a", encoding="utf-8") as queue:
        queue.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _torch_is_available() -> bool:
    """True, если torch уже можно импортировать без загрузки (вшит/установлен)."""
    import importlib.util
    try:
        return importlib.util.find_spec("torch") is not None
    except (ImportError, ValueError):
        return False


def _is_mlx_available() -> bool:
    if not (sys.platform == "darwin" and platform.machine() == "arm64"):
        return False

    import importlib.util

    return (
        importlib.util.find_spec("mlx") is not None
        and importlib.util.find_spec("gigaam_mlx") is not None
    )


def _is_onnx_available() -> bool:
    import importlib.util

    try:
        return (
            importlib.util.find_spec("onnx_asr") is not None
            and importlib.util.find_spec("onnxruntime") is not None
        )
    except (ImportError, ValueError):
        return False


def _saved_asr_backend() -> str:
    """Прочитать backend без импорта Qt и torch-тяжёлой цепочки настроек."""
    try:
        payload = json.loads((_user_config_dir() / "user_settings.json").read_text(encoding="utf-8"))
        backend = str(payload.get("asr_backend") or "").strip().lower() if isinstance(payload, dict) else ""
        if backend in {"auto", "mlx", "onnx", "pytorch"}:
            return backend
    except (OSError, ValueError, TypeError):
        pass
    return (ASR_BACKEND or "auto").strip().lower()


def _saved_onnx_provider() -> str:
    """Прочитать ONNX provider до импорта Qt, torch и ONNX Runtime."""
    try:
        payload = json.loads((_user_config_dir() / "user_settings.json").read_text(encoding="utf-8"))
        provider = str(payload.get("onnx_provider") or "").strip().lower() if isinstance(payload, dict) else ""
        if provider in {"auto", "cpu", "cuda", "tensorrt", "coreml", "directml"}:
            return provider
    except (OSError, ValueError, TypeError):
        pass
    return (ONNX_PROVIDER or "auto").strip().lower()


def _installed_onnx_cuda_variant(provider: str, *, runtime_manager=None) -> str | None:
    """Найти уже установленный CUDA runtime, не запуская загрузку."""
    normalized = (provider or "auto").strip().lower() or "auto"
    if normalized not in {"auto", "cuda", "tensorrt"}:
        return None
    if sys.platform != "win32" and not sys.platform.startswith("linux"):
        return None
    if runtime_manager is None:
        from src.utils import runtime_manager

    variant = runtime_manager.get_selected_variant()
    if not variant or not runtime_manager.is_installed(variant):
        return None
    if runtime_manager.torch_device_for(variant) != "cuda":
        return None
    return variant


def _boot_requires_torch() -> bool:
    backend = _saved_asr_backend()
    if backend == "pytorch":
        return True
    if backend == "onnx":
        return False
    if backend == "mlx":
        return not _is_mlx_available()
    if not (sys.platform == "darwin" and platform.machine() == "arm64"):
        return True
    return not _is_mlx_available()


def run_asr_runtime_smoke() -> dict[str, str]:
    """Exercise bundled MLX native code without downloading model weights."""
    import gigaam_mlx
    import mlx.core as mx

    value = mx.array([1.0, 2.0])
    mx.eval(value)
    return {
        "backend": "mlx",
        "gigaam_mlx": getattr(gigaam_mlx, "__version__", "unknown"),
    }


def run_sortformer_runtime_smoke() -> dict[str, str]:
    """Verify that the frozen full app contains the optional NeMo backend."""
    import importlib

    models = importlib.import_module("nemo.collections.asr.models")
    model_class = models.SortformerEncLabelModel
    return {"sortformer": model_class.__name__}


def run_sortformer_onnx_smoke(provider: str = "cpu") -> dict[str, object]:
    """Download and execute the NeMo-free portable Sortformer runtime."""
    from src.core.diarization.sortformer_onnx import SortformerOnnxDiarizationManager

    manager = SortformerOnnxDiarizationManager(provider=provider)
    try:
        manager.prepare()
        return {"sortformer_onnx": manager.smoke_test()}
    finally:
        manager.unload()


def run_sortformer_model_smoke(audio_path: str, device: str = "mps") -> dict[str, object]:
    """Run the native NeMo Sortformer on a real file and report its device."""
    from src.utils.diarization import SortformerDiarizationManager

    manager = SortformerDiarizationManager(device=device)
    manager.prepare()
    try:
        segments = manager.diarize(audio_path)
        return {
            "backend": "sortformer-nemo",
            "device": manager.device,
            "segments": len(segments),
            "speakers": len({segment.speaker for segment in segments}),
        }
    finally:
        manager.unload()


def run_asr_model_smoke(audio_path: str) -> dict[str, object]:
    """Run the same end-to-end MLX path used by the desktop processor."""
    from src.core.asr.mlx_backend import MLXBackend

    logs: list[str] = []
    backend = MLXBackend(model="rnnt")
    if not backend.load(logger=logs.append):
        raise RuntimeError("MLX backend model smoke load failed")
    try:
        segments = backend.transcribe_longform(audio_path)
        capabilities = backend.capabilities()
        return {
            "backend": "mlx",
            "model": "rnnt",
            "segments": len(segments),
            "segmentation_mode": capabilities.segmentation_mode,
            "segmentation_fallback_reason": capabilities.segmentation_fallback_reason,
            "logs": logs,
        }
    finally:
        backend.unload()


def run_offline_models_smoke(audio_path: str | None = None) -> dict[str, object]:
    """Проверить, что офлайн-сборка работает на привезённых моделях.

    Поднимает всю базовую ONNX-цепочку — распознавание, VAD и кластерную диаризацию —
    и требует, чтобы папка моделей рядом со сборкой действительно нашлась. Без
    такого гейта офлайн-артефакт мог бы уехать в релиз, молча продолжая ходить
    в сеть за весами. Аудио необязательно: без него проверяется, что все модели
    открываются локально, с ним — что цепочка ещё и считает.
    """
    from src.config import ASR_BACKEND, BUNDLED_MODELS_DIR, DIARIZATION_BACKEND
    from src.core.asr.onnx_backend import OnnxBackend
    from src.core.diarization.onnx_embeddings import OnnxSpeakerEmbeddings
    from src.core.diarization.onnx_segmentation import OnnxSegmentation

    if BUNDLED_MODELS_DIR is None:
        raise RuntimeError("Папка моделей рядом со сборкой не найдена")
    if ASR_BACKEND != "onnx" or DIARIZATION_BACKEND != "onnx":
        raise RuntimeError(
            "Офлайн-сборка должна умолчанием выбирать onnx, а выбрала "
            f"asr={ASR_BACKEND}, diarization={DIARIZATION_BACKEND}"
        )

    report: dict[str, object] = {
        "models_dir": str(BUNDLED_MODELS_DIR),
        "asr_backend": ASR_BACKEND,
        "diarization_backend": DIARIZATION_BACKEND,
    }

    backend = OnnxBackend(model="v3_e2e_rnnt")
    if not backend.load():
        raise RuntimeError("ONNX backend не загрузился из привезённых моделей")
    try:
        backend._ensure_vad_segmenter()
        report["vad"] = backend.vad_model
        OnnxSegmentation()._ensure_session()
        OnnxSpeakerEmbeddings()._ensure_model()
        report["diarization_models"] = "ok"

        if audio_path:
            segments = backend.transcribe_longform(audio_path)
            report["segments"] = len(segments)
            report["words"] = sum(
                len(segment.get("words") or []) for segment in segments
            )
    finally:
        backend.unload()
    return report


def run_media_download_smoke(url: str, target_dir: str) -> dict[str, list[str]]:
    """Exercise the bundled yt-dlp path and return downloaded files."""
    from src.utils.media_downloader import MediaDownloader

    result = MediaDownloader().download(url, target_dir)
    return {"files": result.files}


def main():
    """Главная функция запуска приложения."""
    if "--asr-runtime-smoke" in sys.argv:
        print(json.dumps(run_asr_runtime_smoke(), ensure_ascii=False, sort_keys=True))
        return
    if "--sortformer-runtime-smoke" in sys.argv:
        print(json.dumps(run_sortformer_runtime_smoke(), ensure_ascii=False, sort_keys=True))
        return
    if "--sortformer-onnx-smoke" in sys.argv:
        provider = os.environ.get("GIGAAM_SMOKE_ONNX_PROVIDER", "cpu")
        print(
            json.dumps(
                run_sortformer_onnx_smoke(provider),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    if "--sortformer-model-smoke" in sys.argv:
        index = sys.argv.index("--sortformer-model-smoke")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--sortformer-model-smoke requires an audio path")
        device = os.environ.get("GIGAAM_SMOKE_TORCH_DEVICE", "mps")
        print(
            json.dumps(
                run_sortformer_model_smoke(sys.argv[index + 1], device),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    if "--asr-model-smoke" in sys.argv:
        index = sys.argv.index("--asr-model-smoke")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--asr-model-smoke requires an audio path")
        print(json.dumps(run_asr_model_smoke(sys.argv[index + 1]), ensure_ascii=False, sort_keys=True))
        return
    if "--offline-models-smoke" in sys.argv:
        index = sys.argv.index("--offline-models-smoke")
        audio = sys.argv[index + 1] if index + 1 < len(sys.argv) else None
        print(
            json.dumps(
                run_offline_models_smoke(audio),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    if "--media-download-smoke" in sys.argv:
        index = sys.argv.index("--media-download-smoke")
        if index + 2 >= len(sys.argv):
            raise SystemExit("--media-download-smoke requires URL and target directory")
        print(
            json.dumps(
                run_media_download_smoke(sys.argv[index + 1], sys.argv[index + 2]),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return

    early_lock = _try_acquire_instance_lock()
    if early_lock is None:
        _queue_open_request(_argv_open_paths(sys.argv))
        sys.exit(0)

    warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
    warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")
    warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")
    warnings.filterwarnings("ignore", message=".*torchaudio.*deprecated.*")
    warnings.filterwarnings("ignore", message=".*speechbrain.pretrained.*deprecated.*")

    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QApplication.instance() or QApplication(sys.argv)
    app._gigaam_instance_lock_file = early_lock

    # On first launch choose the recognition model before the (larger) PyTorch
    # runtime download. This does not load torch and is persisted for the GUI.
    from src.core.asr.models import ASR_MODELS
    from src.utils.user_settings import UserSettings
    settings = UserSettings()
    if not settings.get_value("asr_model"):
        from PyQt6.QtWidgets import QInputDialog

        model_ids = list(ASR_MODELS)
        labels = [f"{ASR_MODELS[model]} [{model}]" for model in model_ids]
        is_ru = settings.get_value("language", "ru") == "ru"
        selected, accepted = QInputDialog.getItem(
            None,
            "Модель распознавания" if is_ru else "Recognition model",
            "Выберите модель GigaAM:" if is_ru else "Choose a GigaAM model:",
            labels,
            0,
            False,
        )
        if not accepted:
            sys.exit(0)
        settings.set_value("asr_model", model_ids[labels.index(selected)])

    # ONNX Runtime GPU использует CUDA/cuDNN из уже выбранного PyTorch runtime.
    # Для auto ничего не скачиваем: CUDA активируется, только если пользователь
    # ранее выбрал и установил её в меню устройства; иначе ORT продолжит на CPU.
    from src.utils import runtime_manager as rm

    saved_backend = _saved_asr_backend()
    saved_provider = _saved_onnx_provider()
    onnx_cuda_variant = (
        _installed_onnx_cuda_variant(saved_provider, runtime_manager=rm)
        if saved_backend == "onnx"
        else None
    )
    if onnx_cuda_variant:
        rm.activate(onnx_cuda_variant)

    # Если выбранный backend требует PyTorch, и он ещё не установлен — предлагаем выбрать runtime.
    if _boot_requires_torch() and not _torch_is_available():
        from src.gui.device_dialog import ensure_device_ready

        variant = ensure_device_ready()
        if not variant:
            sys.exit(0)
        rm.activate(variant)

    if HF_TOKEN and str(HF_TOKEN).startswith("hf_"):
        from src.utils.pyannote_patch import apply_pyannote_patch
        apply_pyannote_patch()

    from src.gui import run_qt_app
    run_qt_app(app=app)


if __name__ == "__main__":
    main()
