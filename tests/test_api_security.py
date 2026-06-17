"""Тесты исправлений безопасности API (Phase 0.3, 0.4)."""

import importlib

import pytest
from fastapi import HTTPException

api = importlib.import_module("api")


# ---- safe_filename (path traversal) ----

@pytest.mark.parametrize("raw,expected_no_sep", [
    ("../../etc/passwd", "passwd"),
    ("..\\..\\windows\\system32\\cfg", "cfg"),
    ("normal.mp3", "normal.mp3"),
    ("/abs/path/audio.wav", "audio.wav"),
])
def test_safe_filename_strips_paths(raw, expected_no_sep):
    safe = api.safe_filename(raw)
    assert "/" not in safe and "\\" not in safe
    assert safe == expected_no_sep


def test_safe_filename_empty_fallback():
    assert api.safe_filename("") == "upload"
    assert api.safe_filename(None) == "upload"


def test_safe_filename_no_null_byte():
    assert "\x00" not in api.safe_filename("a\x00b.mp3")


# ---- validated_task_id ----

def test_validated_task_id_accepts_uuid_hex():
    import uuid
    tid = uuid.uuid4().hex
    assert api.validated_task_id(tid) == tid


@pytest.mark.parametrize("bad", [
    "../../../etc", "abc", "g" * 32, "../" + "a" * 30, "", "AABB" * 8,
])
def test_validated_task_id_rejects_bad(bad):
    with pytest.raises(HTTPException) as exc:
        api.validated_task_id(bad)
    assert exc.value.status_code == 400


# ---- API key hashing & constant-time compare ----

def test_hash_key_is_sha256_hex():
    h = api._hash_key("gam_test")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_verify_api_key_accept_reject(monkeypatch):
    valid_raw = "gam_secret"
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", {api._hash_key(valid_raw)})
    assert api.verify_api_key(valid_raw) == valid_raw
    with pytest.raises(HTTPException) as exc:
        api.verify_api_key("wrong")
    assert exc.value.status_code == 401


def test_load_api_keys_migrates_plaintext(tmp_path, monkeypatch):
    """Старый plaintext-ключ мигрирует в хэш, но продолжает работать."""
    keyfile = tmp_path / ".api_keys"
    keyfile.write_text("gam_legacyplain\n", encoding="utf-8")
    monkeypatch.setattr(api, "API_KEYS_FILE", keyfile)
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", set())

    api.load_api_keys()

    # В файле теперь только хэш (64 hex), не plaintext
    content = keyfile.read_text(encoding="utf-8").strip()
    assert content == api._hash_key("gam_legacyplain")
    assert "gam_legacyplain" not in content
    # И старый ключ по-прежнему проходит проверку
    assert api.verify_api_key("gam_legacyplain") == "gam_legacyplain"
