"""Streamable HTTP для MCP: guard по API-ключу и монтирование `/mcp` в FastAPI/Starlette.

`build_mcp_asgi` оборачивает транспорт SDK в `_KeyGuard` (чистый ASGI, как
`_UploadGuard` в api.py): без ключа или с неверным — 401 в конверте OpenAI,
OPTIONS (CORS preflight) пропускается. SDK-шный `token_verifier` не используется:
он рассчитан на OAuth resource-server, а у нас общий `KeyStore` с REST.

`mount_mcp` вешает маршрут лениво: бэкенд, сервер и `session_manager.run()`
создаются внутри lifespan хоста, уже после того как он загрузил модель. Менеджер
сессий SDK запускается один раз на экземпляр, поэтому на каждый lifespan строится
новый сервер (TestClient входит в lifespan на каждый `with`).
"""
from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from src.services.api_keys import KeyStore, key_from_headers
from src.services.mcp_backend import LocalBackend
from src.services.mcp_server import build_server

_MiB = 1024 * 1024
DEFAULT_MAX_BODY = 64 * _MiB  # base64 при GIGAAM_MCP_MAX_INLINE_MB=25 — ~34 MiB, с запасом
DEFAULT_MAX_INLINE_MB = 25


def max_body_for(max_inline_bytes: int) -> int:
    """Лимит тела HTTP-запроса под `audio_base64` размером `max_inline_bytes`.

    Base64 раздувает данные в 4/3 плюс JSON-RPC обёртка; ниже 64 МиБ не опускаемся.
    Иначе при большом `GIGAAM_MCP_MAX_INLINE_MB` SDK отвечал бы голым 413 до вызова
    инструмента, минуя контракт `[file_too_large]`. Тот же лимит нужен nginx
    (`client_max_body_size`, см. docs/MCP.md).
    """
    return max(DEFAULT_MAX_BODY, max_inline_bytes * 4 // 3 + _MiB)


def backend_options_from_env(env=os.environ) -> dict:
    """Лимиты и политика `path` для HTTP-режима — из переменных окружения (см. docs/MCP.md)."""
    root = env.get("GIGAAM_MCP_PATH_ROOT")
    return {
        "allow_paths": env.get("GIGAAM_MCP_ALLOW_PATHS") == "1",
        "path_root": Path(root) if root else None,
        "max_inline_bytes": int(float(env.get("GIGAAM_MCP_MAX_INLINE_MB", DEFAULT_MAX_INLINE_MB)) * _MiB),
    }


def _envelope(status: int, message: str, *, type_: str, code: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": type_, "param": None, "code": code}},
                        status_code=status)


class _KeyGuard:
    """Требует `Authorization: Bearer <key>` или `X-API-Key` на каждый запрос к MCP."""

    def __init__(self, app, key_store: KeyStore):
        self.app = app
        self.key_store = key_store

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") != "OPTIONS":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            key = key_from_headers(headers)
            if key is None:
                await _envelope(401, "Missing API key. Send 'Authorization: Bearer <key>' or 'X-API-Key: <key>'.",
                                type_="authentication_error", code="invalid_api_key")(scope, receive, send)
                return
            if not self.key_store.verify(key):
                await _envelope(401, "Incorrect API key provided.",
                                type_="authentication_error", code="invalid_api_key")(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_mcp_asgi(server: MCPServer, key_store: KeyStore, *, max_body: int = DEFAULT_MAX_BODY):
    """Транспорт Streamable HTTP над `server` за `_KeyGuard`.

    Stateless — чтобы nginx и несколько воркеров не ломали сессии; DNS-rebinding
    защита выключена, потому что публичный хост и локальный адрес различаются, а
    доступ и так по ключу. Starlette-обёртку SDK не берём: под `Mount("/mcp")`
    точный `/mcp` отвечал бы 307 на `/mcp/`, и curl/клиенты без follow-redirect
    ломались бы; голый обработчик менеджера сессий от пути не зависит.
    """
    server.streamable_http_app(
        streamable_http_path="/", stateless_http=True, max_request_body_size=max_body,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return _KeyGuard(StreamableHTTPASGIApp(server.session_manager), key_store)


class _LifespanRoute:
    """Точка монтирования: пока lifespan хоста не запущен, отвечает 503 в конверте."""

    def __init__(self):
        self.app = None

    async def __call__(self, scope, receive, send):
        if self.app is None:
            await _envelope(503, "MCP server is not running.", type_="server_error",
                            code="service_unavailable")(scope, receive, send)
            return
        await self.app(scope, receive, send)


def mount_mcp(app: Starlette, path: str, backend_factory: Callable[[], LocalBackend],
              key_store_factory: Callable[[], KeyStore], *, max_body: int | None = None) -> None:
    """Регистрирует `path` и оборачивает lifespan хоста: после его старта строит бэкенд
    и сервер, запускает менеджер сессий SDK на время работы приложения.

    `max_body` по умолчанию выводится из `backend.max_inline_bytes` (`max_body_for`),
    чтобы лимит тела не расходился с `GIGAAM_MCP_MAX_INLINE_MB`."""
    route = _LifespanRoute()
    app.add_route(path, route, include_in_schema=False)
    host_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with host_lifespan(app) as state:  # здесь хост уже загрузил модель и ключи
            backend = backend_factory()
            server = build_server(backend)
            cap = max_body if max_body is not None else max_body_for(backend.max_inline_bytes)
            asgi = build_mcp_asgi(server, key_store_factory(), max_body=cap)
            async with server.session_manager.run():
                route.app = asgi
                try:
                    yield state
                finally:
                    route.app = None

    app.router.lifespan_context = lifespan
