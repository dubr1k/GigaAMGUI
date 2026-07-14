"""Supported GigaAM ASR model variants."""

ASR_MODELS = {
    "v3_e2e_rnnt": "GigaAM v3 e2e RNNT (current)",
    "multilingual_ctc": "GigaAM Multilingual CTC (220M)",
    "multilingual_large_ctc": "GigaAM Multilingual Large CTC (600M)",
}

_ASR_MODEL_ALIASES = {
    "e2e_rnnt": "v3_e2e_rnnt",
}


def validate_asr_model(model: str | None) -> str:
    selected = (model or "v3_e2e_rnnt").strip()
    selected = _ASR_MODEL_ALIASES.get(selected, selected)
    if selected not in ASR_MODELS:
        raise ValueError(f"Unknown ASR model: {selected}")
    return selected
