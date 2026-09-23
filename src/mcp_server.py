"""`python -m src.mcp_server` — MCP-сервер GigaAM: stdio (по умолчанию) или `--http`.

stdio: при старте создаётся только конфигурация загрузчика. Веса загружаются
для каждого transcribe и освобождаются после него; `path` разрешён, ключ не
нужен. stdout принадлежит протоколу: при запуске шум уходит в stderr, затем
дескриптор защищает stdio-транспорт MCP SDK.

`--http`: тот же сервер за `_KeyGuard` на `/mcp` через uvicorn; ключи — из
`API_KEYS_FILE` (тот же файл, что у api.py), `path` — только при
`GIGAAM_MCP_ALLOW_PATHS=1`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from starlette.applications import Starlette

from src import __version__
from src.config import HF_TOKEN
from src.core.model_loader import ModelLoader
from src.services.api_keys import KeyStore
from src.services.mcp_backend import LocalBackend
from src.services.mcp_http import backend_options_from_env, mount_mcp
from src.services.mcp_server import build_server
from src.utils.audio_converter import ffmpeg_available
from src.utils.logger import setup_logger
from src.utils.media_downloader import MediaDownloader
from src.utils.processing_stats import ProcessingStats

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = ROOT / os.getenv("UPLOAD_DIR", "uploads")
API_KEYS_FILE = ROOT / os.getenv("API_KEYS_FILE", ".api_keys")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))  # 2GB
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
DEFAULT_HTTP_PORT = 8765


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.mcp_server",
                                     description="GigaAM MCP server: stdio by default, Streamable HTTP with --http.")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP at /mcp instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="bind address for --http (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT,
                        help=f"port for --http (default: {DEFAULT_HTTP_PORT})")
    parser.add_argument("--config-dir", type=Path, default=None,
                        help="directory with user_settings.json / tui_settings.json / .env for LLM settings "
                             "(default: the app's config directory or GIGAAM_CONFIG_DIR)")
    parser.add_argument("--version", action="version", version=f"GigaAM MCP {__version__}")
    return parser.parse_args(argv)


@contextmanager
def stdout_to_stderr():
    """На время блока fd 1 указывает на stderr: шум загрузки модели не попадает в протокол."""
    sys.stdout.flush()
    wire = os.dup(1)
    os.dup2(2, 1)
    try:
        yield
    finally:
        sys.stdout.flush()
        os.dup2(wire, 1)
        os.close(wire)


def _load_model(logger: logging.Logger) -> ModelLoader:
    """Как lifespan api.py: предупреждения про ffmpeg/HF_TOKEN, загрузка модели, ошибка — исключение."""
    if not ffmpeg_available():
        logger.error("ffmpeg/ffprobe не найдены в PATH — обработка файлов будет невозможна!")
    if not HF_TOKEN or not HF_TOKEN.startswith("hf_"):
        logger.warning("HF_TOKEN не настроен. Диаризация pyannote будет недоступна.")
    logger.info("Загрузка модели GigaAM-v3...")
    loader = ModelLoader()
    if not loader.load_model(logger=logger.info):
        raise RuntimeError("Ошибка загрузки модели")
    logger.info("Модель успешно загружена")
    return loader


def build_backend(*, http_mode: bool, config_dir: Path | None = None,
                  logger: logging.Logger | None = None) -> LocalBackend:
    logger = logger or setup_logger()
    options = backend_options_from_env()
    if not http_mode:
        options["allow_paths"] = True  # локальный агент: файлы с этой же машины
    return LocalBackend(
        model_loader=_load_model(logger) if http_mode else ModelLoader(), stats_manager=ProcessingStats(),
        semaphore=asyncio.Semaphore(MAX_CONCURRENT_TASKS), upload_dir=UPLOAD_DIR,
        media_downloader=MediaDownloader(), loader_factory=ModelLoader, logger=logger, http_mode=http_mode,
        max_file_size=MAX_FILE_SIZE, hf_token=HF_TOKEN, llm_config_dir=config_dir,
        max_concurrent=MAX_CONCURRENT_TASKS, **options)


def build_stdio_backend(config_dir: Path | None = None, logger: logging.Logger | None = None) -> LocalBackend:
    return build_backend(http_mode=False, config_dir=config_dir, logger=logger)


def build_http_app(config_dir: Path | None = None, logger: logging.Logger | None = None) -> Starlette:
    """Starlette-приложение с `/mcp` за ключом; модель грузится в lifespan."""
    app = Starlette()
    mount_mcp(app, "/mcp", lambda: build_backend(http_mode=True, config_dir=config_dir, logger=logger),
              lambda: KeyStore(API_KEYS_FILE).load())
    return app


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.http:
        import uvicorn

        uvicorn.run(build_http_app(args.config_dir), host=args.host, port=args.port, log_level="info")
        return 0

    with stdout_to_stderr():
        server = build_server(build_stdio_backend(args.config_dir))
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
