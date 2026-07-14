import pytest

from src.core.asr.models import validate_asr_model


def test_legacy_default_model_alias_is_normalized():
    assert validate_asr_model("e2e_rnnt") == "v3_e2e_rnnt"


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="Unknown ASR model"):
        validate_asr_model("unknown")
