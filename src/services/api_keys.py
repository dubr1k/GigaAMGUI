"""Хранилище API-ключей: в файле только SHA-256, сравнение constant-time.

Одно на api.py, веб-панель и MCP-guard; формат .api_keys прежний
(по одному hex-хэшу на строку, старые plaintext-строки мигрируются при загрузке).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from collections.abc import Mapping
from pathlib import Path

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def hash_key(key: str) -> str:
    """SHA-256 хэш ключа (в файле и памяти хранятся только хэши)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def key_from_headers(headers: Mapping[str, str]) -> str | None:
    """Bearer из Authorization, иначе X-API-Key; регистр имён заголовков не важен."""
    lowered = {k.lower(): v for k, v in headers.items()}
    auth = lowered.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    key = (lowered.get("x-api-key") or "").strip()
    return key or None


class KeyStore:
    """Набор хэшей ключей, привязанный к файлу."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.hashes: set[str] = set()

    def load(self) -> KeyStore:
        """Читает файл; plaintext-строки хэширует и перезаписывает файл; без файла создаёт первый ключ."""
        if not self.path.exists():
            self.create_default()
            return self
        migrated = False
        hashes: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            if _HASH_RE.match(line):
                hashes.add(line)
            else:
                # Старый ключ в открытом виде — мигрируем в хэш
                hashes.add(hash_key(line))
                migrated = True
        self.hashes = hashes
        if migrated:
            self.save()
            print("API-ключи мигрированы в хэшированный вид (.api_keys)")
        return self

    def save(self) -> None:
        """Пишет хэши в файл (только владелец может читать)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            for key_hash in sorted(self.hashes):
                handle.write(f"{key_hash}\n")
        os.chmod(self.path, 0o600)

    def add(self, key: str) -> None:
        self.hashes.add(hash_key(key))

    def create_default(self) -> str:
        """Генерирует первый ключ, сохраняет его хэш и возвращает сырой ключ (печатается один раз)."""
        key = f"gam_{uuid.uuid4().hex}"
        self.hashes = {hash_key(key)}
        self.save()
        print(f"\n{'=' * 60}")
        print("ПЕРВЫЙ API КЛЮЧ СОЗДАН (показывается только один раз):")
        print(f"  {key}")
        print("Сохраните его в безопасном месте! В файле хранится только хэш.")
        print(f"{'=' * 60}\n")
        return key

    def verify(self, key: str | None) -> bool:
        """Constant-time сравнение со всеми хэшами; пустой/None — False."""
        if not key:
            return False
        candidate = hash_key(key)
        return any(hmac.compare_digest(candidate, valid) for valid in self.hashes)
