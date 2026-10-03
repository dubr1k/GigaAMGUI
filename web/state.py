"""Настройки и разделяемое состояние веб-панели.

Один изменяемый объект `state`: модули web/* читают через него пути, лимиты и
объекты времени работы (загрузчик модели, семафоры, хранилище ключей) в момент
вызова, а не копируют их в свои глобали при импорте. Поэтому тесты подменяют
`state.upload_dir`, а не одноимённую глобаль в каждом модуле, и lifespan
заполняет `state.model_loader` для всех сразу.

Обязательные настройки входа проверяются при импорте, как и раньше в
web_app.py: без WEB_SECRET (не короче 32 байт), WEB_USERNAME и WEB_PASSWORD
панель не стартует.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from limits import parse_many

from src.config import HF_TOKEN
from src.core.model_loader import ModelLoader

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

_env_path = ROOT / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=False)


def _flag(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


def validated_login_rate_limit(value: str | None) -> str:
    """Лимит попыток входа; неразборная строка — отказ при старте, а не молча без лимита
    (slowapi при ошибке разбора лимит просто не применяет)."""
    limit = (value or "").strip() or "10/minute"
    try:
        parse_many(limit)
    except ValueError as exc:
        raise ValueError(f"WEB_LOGIN_RATE_LIMIT={limit!r} is not a valid rate limit (e.g. '10/minute').") from exc
    return limit


class WebState:
    """Настройки из окружения и объекты, которые создаёт lifespan."""

    def __init__(self) -> None:
        # ---- вход ----
        self.port = int(os.getenv("WEB_PORT", "8000"))
        self.secret = os.getenv("WEB_SECRET", "")
        self.username = os.getenv("WEB_USERNAME", "")
        self.password = os.getenv("WEB_PASSWORD", "")
        self.jwt_expire_hours = int(os.getenv("JWT_EXPIRE_HOURS", "72"))
        # secure-cookie требует HTTPS (или localhost). За TLS-прокси/на HTTPS оставляем
        # True; при доступе по чистому HTTP с не-localhost домена браузер молча выкинет
        # cookie и логин зациклится — тогда выставите COOKIE_SECURE=0.
        self.cookie_secure = os.getenv("COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no")
        self.login_rate_limit = validated_login_rate_limit(os.getenv("WEB_LOGIN_RATE_LIMIT"))
        # Origin-ы (scheme://host[:port]) кроме самой панели, которым можно слать изменяющие
        # запросы с cookie сессии и читать API через CORS (фронтенд разработки). Обычно пусто.
        self.trusted_origins: tuple[str, ...] = tuple(
            origin.strip().rstrip("/") for origin in os.getenv("WEB_TRUSTED_ORIGINS", "").split(",")
            if origin.strip()
        )

        # ---- каталоги ----
        self.upload_dir = ROOT / os.getenv("UPLOAD_DIR", "uploads")
        self.results_dir = ROOT / os.getenv("RESULTS_DIR", "results")
        # Ключи для /mcp — тот же файл, что у api.py (в контейнере API_KEYS_FILE=/data/.api_keys, persist)
        self.api_keys_file = ROOT / os.getenv("API_KEYS_FILE", ".api_keys")
        self.stats_file = os.getenv("STATS_FILE")

        # ---- лимиты ----
        self.max_file_size = int(os.getenv("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))
        # Транскрипты для LLM-вкладки — текст; лимит на всё тело запроса /api/llm/process
        self.max_llm_body_size = int(os.getenv("WEB_MAX_LLM_BODY_SIZE", str(50 * 1024 * 1024)))
        self.max_concurrent_tasks = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
        # Одновременные вызовы LLM-провайдеров LLM-вкладки (свой семафор, не общий с ASR)
        self.max_concurrent_llm = int(os.getenv("WEB_MAX_CONCURRENT_LLM", "2"))
        # 1 — пути/аргументы CLI и «инструменты агента» из LLM-формы принимаются как есть
        # (прежнее поведение: сессия = запуск любой команды на сервере). По умолчанию выкл.
        self.allow_client_llm_cli = _flag("WEB_ALLOW_CLIENT_LLM_CLI")

        # ---- окружение моделей ----
        self.hf_token = HF_TOKEN
        self.loader_factory = ModelLoader

        # ---- создаёт lifespan ----
        self.model_loader = None
        self.stats_manager = None
        self.media_downloader = None
        self.processing_semaphore: asyncio.Semaphore | None = None
        self.llm_semaphore: asyncio.Semaphore | None = None
        self.key_store = None

    def validate(self) -> None:
        if len(self.secret.encode("utf-8")) < 32:
            raise RuntimeError("WEB_SECRET must be set and contain at least 32 bytes")
        if not self.username:
            raise RuntimeError("WEB_USERNAME must be set")
        if not self.password:
            raise RuntimeError("WEB_PASSWORD must be set")

    # Производные пути — свойства: подмена results_dir в тестах сдвигает их вместе с ним
    @property
    def tasks_index_path(self) -> Path:
        return self.results_dir / ".tasks_index.json"

    @property
    def deleted_tasks_path(self) -> Path:
        return self.results_dir / ".deleted_tasks.json"

    @property
    def llm_results_dir(self) -> Path:
        return self.results_dir / "llm"

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(exist_ok=True)
        self.results_dir.mkdir(exist_ok=True)
        self.llm_results_dir.mkdir(exist_ok=True)


state = WebState()
state.validate()
state.ensure_dirs()
