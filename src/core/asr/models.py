"""Supported GigaAM ASR model variants."""

from pathlib import Path

ASR_MODELS = {
    "v3_e2e_rnnt": "GigaAM v3 e2e RNNT (current)",
    "multilingual_ctc": "GigaAM Multilingual CTC (220M)",
    "multilingual_large_ctc": "GigaAM Multilingual Large CTC (600M)",
    "v4_rnnt": "GigaAM v4 RNNT (238M)",
    "multilingual_v2_rnnt": "GigaAM Multilingual v2 RNNT (776M)",
}

# Both repositories execute custom Transformers code. Pin the code and weights
# to the same reviewed revision instead of trusting a moving branch.
TRANSFORMERS_ASR_MODELS = {
    "v4_rnnt": ("ai-sage/GigaAM-v4", "9fe3ebf50c9e0cce5eed60d546938632238b6a96"),
    "multilingual_v2_rnnt": (
        "ai-sage/GigaAM-Multilingual-v2",
        "c21483b5e185ff32f6fb2e2c801f92f4e7fbfee8",
    ),
}

# GigaAM's Transformers loader needs both tokenizers and its VAD assets even
# when the application segments audio itself and decodes only the e2e head.
TRANSFORMERS_ASR_FILES = (
    "config.json",
    "model.safetensors",
    "modeling_gigaam.py",
    "e2e_tokenizer.model",
    "raw_tokenizer.model",
    "vad.safetensors",
    "vad_config.json",
)


def snapshot_missing_files(snapshot: Path, files: tuple[str, ...]) -> tuple[str, ...]:
    """List absent, broken-link, or empty files in a pinned model snapshot."""
    missing = []
    for name in files:
        path = snapshot / name
        try:
            if not path.is_file() or path.stat().st_size == 0:
                missing.append(name)
        except OSError:
            missing.append(name)
    return tuple(missing)

ONNX_ASR_MODELS = {
    "v3_e2e_rnnt": "gigaam-v3-e2e-rnnt",
    "multilingual_ctc": "gigaam-multilingual-ctc",
    "multilingual_large_ctc": "gigaam-multilingual-large-ctc",
}

ONNX_ASR_REPOS = {
    "v3_e2e_rnnt": "istupakov/gigaam-v3-onnx",
    "multilingual_ctc": "istupakov/gigaam-multilingual-ctc-onnx",
    "multilingual_large_ctc": "istupakov/gigaam-multilingual-large-ctc-onnx",
}

# Older installations store the short GigaAM name in .env. Keep it working
# while persisting the explicit v3 name in new settings.
_ASR_MODEL_ALIASES = {
    "e2e_rnnt": "v3_e2e_rnnt",
}


def validate_asr_model(model: str | None) -> str:
    selected = (model or "v3_e2e_rnnt").strip()
    selected = _ASR_MODEL_ALIASES.get(selected, selected)
    if selected not in ASR_MODELS:
        raise ValueError(f"Unknown ASR model: {selected}")
    return selected


def onnx_model_name(model: str | None) -> str:
    """Вернуть идентификатор модели, поддерживаемый ``onnx-asr``."""

    selected = validate_asr_model(model)
    if selected not in ONNX_ASR_MODELS:
        raise ValueError(f"Модель {selected} недоступна в ONNX backend; выберите PyTorch")
    return ONNX_ASR_MODELS[selected]


def onnx_model_repo(model: str | None) -> str:
    """Вернуть Hugging Face repo, соответствующий выбранной ONNX-модели."""

    selected = validate_asr_model(model)
    if selected not in ONNX_ASR_REPOS:
        raise ValueError(f"Модель {selected} недоступна в ONNX backend; выберите PyTorch")
    return ONNX_ASR_REPOS[selected]
