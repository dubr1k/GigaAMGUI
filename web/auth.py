"""Вход в веб-панель: JWT-сессия, проверка источника и гард тела запроса.

Сессия — JWT (HS256, `state.secret`) в httponly-cookie `gigaam_token` или в
`Authorization: Bearer`. `BodyGuard` (чистый ASGI) проверяет авторизацию,
источник и Content-Length изменяющих запросов /api ДО чтения тела; `limiter`
ограничивает попытки входа.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from web.state import state


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def create_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": datetime.utcnow() + timedelta(hours=state.jwt_expire_hours),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, state.secret, algorithm="HS256")


def verify_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, state.secret, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def _bearer_user(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return verify_token(auth_header[7:])
    return None


def authenticated_user(request: Request) -> str | None:
    """Пользователь из JWT в cookie или в `Authorization: Bearer`; None — не авторизован."""
    token = request.cookies.get("gigaam_token")
    if token:
        username = verify_token(token)
        if username:
            return username
    return _bearer_user(request)


def _hostname(value: str | None) -> str | None:
    """Имя хоста без порта из `Host`/`X-Forwarded-Host` (первого из списка)."""
    if not value:
        return None
    try:
        return urlsplit("//" + value.split(",")[0].strip()).hostname
    except ValueError:
        return None


def _same_origin(request: Request) -> bool:
    """Запрос пришёл со страницы самой панели (или из WEB_TRUSTED_ORIGINS).

    Главный признак — `Sec-Fetch-Site`: его ставит сам браузер (страница
    подделать не может), и он не зависит от того, передаёт ли reverse proxy
    `Host` (nginx без `proxy_set_header Host` шлёт 127.0.0.1:8001).
    Старые браузеры его не шлют — тогда источник берётся из Origin, иначе
    Referer, и сравнивается только имя хоста с `Host`/`X-Forwarded-Host`:
    схема и порт за TLS-прокси у браузера и у приложения разные. Без Origin и
    Referer запрос не из браузера (curl, скрипт): CSRF — атака через чужой
    браузер, а он на POST/DELETE шлёт Origin.
    """
    source = request.headers.get("origin") or request.headers.get("referer")
    source_origin = None
    source_host = None
    if source and source != "null":
        try:
            parsed = urlsplit(source)
            source_origin = f"{parsed.scheme}://{parsed.netloc}"
            source_host = parsed.hostname
        except ValueError:
            pass
    if source_origin in state.trusted_origins:
        return True

    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site in ("same-origin", "none")

    if not source:
        return True
    own_hosts = {_hostname(request.headers.get("host")), _hostname(request.headers.get("x-forwarded-host"))}
    return source_host is not None and source_host in own_hosts - {None}


async def require_auth(request: Request) -> str:
    """Зависимость: проверяет авторизацию через cookie или заголовок."""
    username = authenticated_user(request)
    if username:
        return username
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не авторизован",
    )


# Запас над лимитом тела на multipart-обвязку (как в api.py)
_CONTENT_LENGTH_SLACK = 1024 * 1024
# Тело запросов /api без файлов (форма URL, проверка CLI и т.п.)
_SMALL_BODY_LIMIT = 1024 * 1024
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Без сессии: вход и выход
_AUTH_EXEMPT_PATHS = frozenset({"/api/auth/login", "/api/auth/logout"})


def _body_limit(path: str) -> int:
    if path == "/api/upload":
        return state.max_file_size + _CONTENT_LENGTH_SLACK
    if path == "/api/llm/process":
        return state.max_llm_body_size + _CONTENT_LENGTH_SLACK
    return _SMALL_BODY_LIMIT


class BodyGuard:
    """Авторизация, источник и Content-Length изменяющих запросов /api ДО чтения тела.

    Чистый ASGI, как `_UploadGuard` в api.py. FastAPI разбирает тело формы
    (multipart спулится во временную директорию — в Docker это tmpfs /tmp)
    раньше, чем выполняется Depends(require_auth), поэтому без гарда любой
    без сессии мог залить сколько угодно байт и получить 401 только после
    записи. Здесь — та же проверка JWT, что у require_auth, и лимит по
    заголовку; точный размер файла по-прежнему считает _save_upload при
    записи (тело без Content-Length, chunked, проверяется только им).

    Запрос, авторизованный cookie, должен прийти со страницы панели
    (`_same_origin`), иначе 403: cookie браузер подставит и в запрос с чужого
    сайта. SameSite=Lax этого не закрывает для соседних поддоменов (один
    «сайт»). Действующий `Authorization: Bearer` проверку снимает — его
    чужая страница подставить не может; мусорный Bearer при живой cookie — нет.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope.get("method") in _UNSAFE_METHODS
            and scope.get("path", "").startswith("/api/")
            and scope.get("path") not in _AUTH_EXEMPT_PATHS
        ):
            request = Request(scope)
            if authenticated_user(request) is None:
                await JSONResponse({"detail": "Не авторизован"}, status_code=401)(scope, receive, send)
                return
            if _bearer_user(request) is None and not _same_origin(request):
                await JSONResponse(
                    {"detail": "Запрос с чужого источника (Origin) отклонён; см. WEB_TRUSTED_ORIGINS"},
                    status_code=403,
                )(scope, receive, send)
                return
            content_length = request.headers.get("content-length", "")
            limit = _body_limit(scope["path"])
            if content_length.isdigit() and int(content_length) > limit:
                await JSONResponse(
                    {"detail": f"Запрос слишком большой (макс. {limit / 1024 / 1024:.0f} MB)"},
                    status_code=413,
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Лимит попыток входа (декоратор на /api/auth/login); app.state.limiter — для slowapi
limiter = Limiter(key_func=get_remote_address, headers_enabled=True)


async def login_rate_limited(request: Request, exc: RateLimitExceeded):
    """Обработчик RateLimitExceeded: 429 с понятным текстом для формы входа."""
    response = JSONResponse({"detail": "Слишком много попыток входа. Повторите позже."}, status_code=429)
    # Retry-After / X-RateLimit-* — как у api.py
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is not None:
        response = request.app.state.limiter._inject_headers(response, view_rate_limit)
    return response
