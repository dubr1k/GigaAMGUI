"""Конверт ошибок OpenAI для REST API (`api.py`).

Любая ошибка — `{"error": {"message", "type", "param", "code"}}`, как у OpenAI,
чтобы SDK и клиенты разбирали её штатно. Здесь — исключение `OpenAIError`,
тип по HTTP-статусу и обработчики ошибок FastAPI/Starlette, общего с MCP слоя
(`BackendError`), валидации формы и лимитера. Обработчик необработанных
исключений остаётся в api.py: он зависит от его логгера и API_DEBUG.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.services.mcp_backend import BackendError


class OpenAIError(Exception):
    def __init__(self, status_code: int, message: str, *, type_: str = "invalid_request_error",
                 param: str | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.type = type_
        self.param = param
        self.code = code

    def payload(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.type, "param": self.param, "code": self.code}}


def openai_error(status: int, message: str, *, type_: str = "invalid_request_error",
                 param: str | None = None, code: str | None = None) -> OpenAIError:
    return OpenAIError(status, message, type_=type_, param=param, code=code)


_STATUS_TYPES = {401: "authentication_error", 429: "rate_limit_error"}


def type_for_status(status: int) -> str:
    if status in _STATUS_TYPES:
        return _STATUS_TYPES[status]
    return "server_error" if status >= 500 else "invalid_request_error"


async def _openai_error_handler(_: Request, exc: OpenAIError):
    return JSONResponse(exc.payload(), status_code=exc.status_code)


async def _backend_error_handler(_: Request, exc: BackendError):
    # Ошибки общего с MCP слоя — те же коды и параметры, что раньше поднимал api.py сам
    err = openai_error(exc.status, exc.message, type_=type_for_status(exc.status), param=exc.param, code=exc.code)
    return JSONResponse(err.payload(), status_code=exc.status)


async def _http_error_handler(_: Request, exc: StarletteHTTPException):
    err = openai_error(exc.status_code, str(exc.detail), type_=type_for_status(exc.status_code))
    return JSONResponse(err.payload(), status_code=exc.status_code)


async def _validation_handler(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query")]
    err = openai_error(422, first.get("msg", "Invalid request"), param=".".join(loc) or None)
    return JSONResponse(err.payload(), status_code=422)


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    err = openai_error(429, f"Rate limit exceeded: {exc.detail}", type_="rate_limit_error", code="rate_limit_exceeded")
    response = JSONResponse(err.payload(), status_code=429)
    # Retry-After / X-RateLimit-* — по ним OpenAI SDK делает backoff
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is not None:
        response = request.app.state.limiter._inject_headers(response, view_rate_limit)
    return response


def install_handlers(app: FastAPI) -> None:
    """Все ошибки приложения — в конверте OpenAI (кроме необработанных исключений)."""
    app.add_exception_handler(OpenAIError, _openai_error_handler)
    app.add_exception_handler(BackendError, _backend_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
