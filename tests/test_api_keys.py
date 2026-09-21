"""KeyStore: hashed API keys shared by api.py, web panel and the MCP guard."""
import os
import stat

from src.services.api_keys import KeyStore, hash_key, key_from_headers


def test_hash_key_is_sha256_hex():
    h = hash_key("gam_test")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_missing_file_creates_a_default_key(tmp_path, capsys):
    store = KeyStore(tmp_path / ".api_keys").load()
    raw = capsys.readouterr().out
    assert "gam_" in raw
    key = next(line.strip() for line in raw.splitlines() if line.strip().startswith("gam_"))
    assert store.verify(key) is True
    assert (tmp_path / ".api_keys").read_text().strip() == hash_key(key)
    assert stat.S_IMODE(os.stat(tmp_path / ".api_keys").st_mode) == 0o600


def test_plaintext_lines_are_migrated(tmp_path):
    path = tmp_path / ".api_keys"
    path.write_text("gam_plain\n" + hash_key("gam_hashed") + "\n")
    store = KeyStore(path).load()
    assert store.verify("gam_plain") and store.verify("gam_hashed")
    assert "gam_plain" not in path.read_text()


def test_verify_rejects_wrong_and_empty(tmp_path):
    store = KeyStore(tmp_path / ".api_keys")
    store.add("gam_ok")
    assert store.verify("gam_ok")
    assert not store.verify("gam_no") and not store.verify("") and not store.verify(None)


def test_key_from_headers_prefers_bearer():
    assert key_from_headers({"authorization": "Bearer abc"}) == "abc"
    assert key_from_headers({"authorization": "bearer abc"}) == "abc"
    assert key_from_headers({"authorization": "Basic zzz", "x-api-key": "k"}) == "k"
    assert key_from_headers({"x-api-key": " k "}) == "k"
    assert key_from_headers({}) is None
    assert key_from_headers({"authorization": "Bearer "}) is None
