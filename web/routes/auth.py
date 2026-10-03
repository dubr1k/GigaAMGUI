"""Вход и выход: /api/auth/login, /api/auth/logout, /api/auth/check."""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web import auth
from web.state import state

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
@auth.limiter.limit(state.login_rate_limit)
async def login(request: Request, req: LoginRequest):
    # Лимит считает все попытки с адреса: без него пароль единственной учётной
    # записи перебирался со скоростью сети. За прокси без доверенного
    # X-Forwarded-For адрес у всех общий — лимит тогда общий на панель.
    if req.username == state.username and hmac.compare_digest(auth.hash_password(req.password), auth.hash_password(state.password)):
        token = auth.create_token(req.username)
        response = JSONResponse({"ok": True, "username": req.username})
        response.set_cookie(
            key="gigaam_token",
            value=token,
            httponly=True,
            secure=state.cookie_secure,
            samesite="lax",
            max_age=state.jwt_expire_hours * 3600,
        )
        return response
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверное имя пользователя или пароль",
    )


@router.post("/api/auth/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("gigaam_token")
    return response


@router.get("/api/auth/check")
async def auth_check(user: str = Depends(auth.require_auth)):
    return {"ok": True, "username": user}
