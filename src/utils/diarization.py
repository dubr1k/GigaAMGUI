"""
Совместимый вход в диаризацию: реализация живёт в ``src.core.diarization``.

- pyannote: ``core.diarization.pyannote_backend``;
- NVIDIA Sortformer через NeMo: ``core.diarization.sortformer_nemo``;
- ONNX и Sortformer-ONNX: ``core.diarization.onnx_backend`` / ``sortformer_onnx``;
- выбор backend-а: ``core.diarization.factory.create_diarization_backend``.

Модуль оставлен ради старых импортов (``src.utils._LAZY``, GUI, тесты) и
сам ничего не реализует: core больше не импортирует utils.diarization.
"""

from contextlib import nullcontext  # noqa: F401 — тесты подменяют контекст инференса

from ..core.diarization.base import SpeakerSegment  # noqa: F401
from ..core.diarization.factory import (  # noqa: F401
    create_diarization_backend,
    should_use_sortformer_onnx,
)
from ..core.diarization.hf_access import _DIARIZATION_REQUIRED_REPOS, diagnose_hf_access  # noqa: F401
from ..core.diarization.mapping import (  # noqa: F401
    MAX_SPEAKER_SNAP_DISTANCE_SEC,
    MIN_SPEAKER_TURN_SEC,
    UNKNOWN_SPEAKER,
    SpeakerMappingMixin,
)
from ..core.diarization.names import DIARIZATION_BACKENDS, normalize_diarization_backend  # noqa: F401
from ..core.diarization.pyannote_backend import (  # noqa: F401
    _DIARIZATION_MODEL_ID,
    DiarizationManager,
    _annotation_from_pipeline_output,
)
from ..core.diarization.sortformer_nemo import (  # noqa: F401
    _SORTFORMER_MODEL_ID,
    SortformerDiarizationManager,
)


def get_diarization_manager(
    hf_token: str | None = None,
    device: str = "auto",
    backend: str = "pyannote",
    **kwargs,
):
    """Старое имя ``create_diarization_backend`` (provider, model_dir, nemo_available …)."""
    return create_diarization_backend(backend, hf_token=hf_token, device=device, **kwargs)
