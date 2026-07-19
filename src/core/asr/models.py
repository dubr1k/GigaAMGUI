"""Supported GigaAM ASR model variants."""

ASR_MODELS = {
    "v3_e2e_rnnt": "GigaAM v3 e2e RNNT (current)",
    "multilingual_ctc": "GigaAM Multilingual CTC (220M)",
    "multilingual_large_ctc": "GigaAM Multilingual Large CTC (600M)",
}

ONNX_ASR_MODELS = {
    "v3_e2e_rnnt": "gigaam-v3-e2e-rnnt",
    "multilingual_ctc": "gigaam-multilingual-ctc",
    "multilingual_large_ctc": "gigaam-multilingual-large-ctc",
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

    return ONNX_ASR_MODELS[validate_asr_model(model)]
