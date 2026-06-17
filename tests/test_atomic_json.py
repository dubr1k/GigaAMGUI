"""Тесты атомарной записи/устойчивой загрузки JSON (Phase 0.8)."""

import json
import os

from src.utils.atomic_json import load_json, save_json_atomic


def test_roundtrip(tmp_path):
    path = str(tmp_path / "data.json")
    data = {"history": [1, 2, 3], "ключ": "значение"}
    save_json_atomic(path, data)
    assert load_json(path, None) == data


def test_load_missing_returns_default(tmp_path):
    path = str(tmp_path / "missing.json")
    assert load_json(path, {"default": True}) == {"default": True}


def test_load_corrupt_returns_default(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{ это не валидный json ", encoding="utf-8")
    assert load_json(str(path), {"recovered": True}) == {"recovered": True}


def test_no_leftover_temp_files(tmp_path):
    path = str(tmp_path / "data.json")
    save_json_atomic(path, {"a": 1})
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert leftovers == []


def test_overwrite_preserves_validity(tmp_path):
    path = str(tmp_path / "data.json")
    save_json_atomic(path, {"v": 1})
    save_json_atomic(path, {"v": 2})
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"v": 2}
