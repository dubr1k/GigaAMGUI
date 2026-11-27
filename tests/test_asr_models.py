import pytest

from src.core.asr.models import onnx_model_name, validate_asr_model


def test_legacy_default_model_alias_is_normalized():
    assert validate_asr_model("e2e_rnnt") == "v3_e2e_rnnt"


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="Unknown ASR model"):
        validate_asr_model("unknown")


@pytest.mark.parametrize(
    ("application_name", "onnx_name"),
    [
        ("v3_e2e_rnnt", "gigaam-v3-e2e-rnnt"),
        ("e2e_rnnt", "gigaam-v3-e2e-rnnt"),
        ("multilingual_ctc", "gigaam-multilingual-ctc"),
        ("multilingual_large_ctc", "gigaam-multilingual-large-ctc"),
    ],
)
def test_onnx_model_mapping_covers_public_models(application_name, onnx_name):
    assert onnx_model_name(application_name) == onnx_name


@pytest.mark.parametrize("model", ["v4_rnnt", "multilingual_v2_rnnt"])
def test_transformers_models_are_not_mapped_to_a_different_onnx_model(model):
    from src.core.asr.models import onnx_model_repo

    assert validate_asr_model(model) == model
    with pytest.raises(ValueError, match="недоступна в ONNX"):
        onnx_model_name(model)
    with pytest.raises(ValueError, match="недоступна в ONNX"):
        onnx_model_repo(model)
