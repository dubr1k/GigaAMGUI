"""Граница импорта рантайма: эти модули грузятся до выбора torch-рантайма.

app.py, worker и CLI импортируют их до активации выбранного PyTorch-варианта
(cpu/cu124/cu128). Любой ``import torch``/pyannote/NeMo на уровне модуля
загрузил бы чужой torch раньше времени, а горячая смена рантайма без
перезапуска перестала бы работать. Тест блокирует torch-цепочку целиком.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

BANNED = (
    "torch",
    "torchaudio",
    "torchvision",
    "pyannote",
    "nemo",
    "gigaam",
    "gigaam_mlx",
    "mlx",
    "lightning",
    "pytorch_lightning",
)

MODULES = (
    "src.utils.runtime_manager",
    "src.utils.torch_downloader",
    "src.utils.model_cache",
    "src.utils.diarization",
    "src.utils.audio_converter",
    "src.utils.llm_client",
    "src.utils.cancellation",
    "src.core.processor",
    "src.core.model_loader",
    "src.core.export",
    "src.core.progress",
    "src.core.devices",
    "src.core.runtime_options",
    "src.core.preprocessing_messages",
    "src.core.asr.longform",
    "src.core.asr.onnx_loading",
    "src.core.diarization.factory",
    "src.core.diarization.names",
    "src.core.diarization.hf_access",
    "src.core.diarization.pyannote_backend",
    "src.core.diarization.sortformer_nemo",
    "src.services.transcription_service",
)


def test_runtime_sensitive_modules_import_without_torch_chain():
    script = textwrap.dedent(
        f"""
        import sys

        BANNED = {BANNED!r}

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BANNED:
                    raise ImportError(f"imported before runtime activation: {{name}}")
                return None

        sys.meta_path.insert(0, Blocker())
        for module in {MODULES!r}:
            __import__(module)
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=300,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
