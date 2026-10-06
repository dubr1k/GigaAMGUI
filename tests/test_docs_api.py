"""Docs must describe the API that ships: no references to the removed /api/v1 routes."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRACKED_DOCS = [
    "README.md",
    "README_EN.md",
    "docs/API.md",
    "docs/START_HERE.md",
    "postman/README.md",
    "postman/GigaAM_API.postman_collection.json",
]


def test_docs_do_not_mention_removed_api():
    for rel in TRACKED_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "/api/v1/" not in text, rel


def test_api_reference_covers_contract():
    text = (ROOT / "docs/API.md").read_text(encoding="utf-8")
    for needle in ["/v1/audio/transcriptions", "/v1/models", "Authorization: Bearer", "verbose_json",
                   "diarized_json", "stream=true", "transcript.text.delta", "from openai import OpenAI",
                   "diarize", "invalid_api_key", "model_not_found"]:
        assert needle in text, needle


def test_removed_docs_are_gone():
    for rel in ["docs/API_GUIDE.md", "docs/API_QUICKSTART.md", "docs/POSTMAN_GUIDE.md", "docs/START_WITH_POSTMAN.md"]:
        assert not (ROOT / rel).exists(), rel


def test_postman_collection_targets_new_routes():
    coll = json.loads((ROOT / "postman/GigaAM_API.postman_collection.json").read_text(encoding="utf-8"))
    dump = json.dumps(coll)
    assert "/v1/audio/transcriptions" in dump and "/v1/models" in dump and "Bearer" in dump
