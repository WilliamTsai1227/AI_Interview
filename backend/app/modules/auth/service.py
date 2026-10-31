import datetime as dt
import ipaddress
import uuid

from fastapi import Request, Response

from app.core import db, redis
from app.core.config import settings
from app.core.errors import AppError
from app.core.security import (create_access_token, hash_password, new_refresh_token, sha256,
                               verify_password)
from app.modules.auth import repository as repo

COOKIE = "refresh_token"
COOKIE_PATH = "/api/v1/auth"


def user_out(u) -> dict:
    return {"id": str(u["id"]), "email": u["email"], "display_name": u["display_name"], "plan": u["plan"],
            "role": u["role"]}


def _client_ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:      # unix socket、代理或測試環境
        return None


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=settings.refresh_token_days * 86400, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path=COOKIE_PATH)


async def _issue(conn, user, request: Request, response: Response, family_id: uuid.UUID | None = None) -> str:
    token, h = new_refresh_token()
    await repo.insert_refresh(conn, user["id"], family_id or uuid.uuid4(), h, settings.refresh_token_days,
                              request.headers.get("user-agent"), _client_ip(request))
    _set_cookie(response, token)
    return create_access_token(user["id"], user["role"])


async def _event(conn, name: str, user_id, request: Request) -> None:
    await conn.execute(
        "INSERT INTO app_events (user_id, event_name, request_id, ip, user_agent) VALUES ($1, $2, $3, $4::inet, $5)",
        user_id, name, getattr(request.state, "request_id", None), _client_ip(request),
        (request.headers.get("user-agent") or "")[:300])


async def register(email: str, password: str, display_name: str, request: Request, response: Response) -> dict:
    await redis.rate_limit("register", _client_ip(request) or "unknown")
    if len(password) < 8:
        raise AppError(400, "VALIDATION_ERROR", "密碼至少要 8 碼", {"fields": {"password": "至少 8 碼"}})
    async with db.transaction() as conn:
        if await repo.get_user_by_email(conn, email):
            raise AppError(409, "EMAIL_TAKEN", "這個 Email 已經註冊過，直接登入就好（若之前用 Google 登入，請按「使用 Google 繼續」）")
        user = await repo.insert_user(conn, email, hash_password(password), display_name.strip() or email.split("@")[0])
        access = await _issue(conn, user, request, response)
        await _event(conn, "auth.register", user["id"], request)
    return {"access_token": access, "user": user_out(user)}


async def login(email: str, password: str, request: Request, response: Response) -> dict:
    await redis.rate_limit("login", _client_ip(request) or "unknown")
    eh = sha256(email.lower())
    if await redis.login_fail_count(eh) >= 5:
        raise AppError(429, "RATE_LIMITED", "登入失敗太多次，請 15 分鐘後再試", headers={"Retry-After": "900"})
    async with db.transaction() as conn:
        user = await repo.get_user_by_email(conn, email)
        if user and user["status"] == "active" and not user["password_hash"]:
            raise AppError(401, "USE_GOOGLE", "這個帳號是用 Google 登入的，請按「使用 Google 繼續」")
        if not user or user["status"] != "active" or not verify_password(user["password_hash"], password):
            await redis.login_failed(eh)
            raise AppError(401, "INVALID_CREDENTIALS", "Email 或密碼不正確")
        await repo.touch_login(conn, user["id"])
        access = await _issue(conn, user, request, response)
        await _event(conn, "auth.login", user["id"], request)
    await redis.login_reset(eh)
    return {"access_token": access, "user": user_out(user)}


async def refresh(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE)
    if not token:
        raise AppError(401, "UNAUTHENTICATED", "請先登入")
    async with db.transaction() as conn:
        rt = await repo.get_refresh_for_update(conn, sha256(token))
        if not rt:
            raise AppError(401, "UNAUTHENTICATED", "請重新登入")
        if rt["revoked_at"] is not None:            # 已撤銷的 token 被重用 → 撤銷整個 family
            await repo.revoke_family(conn, rt["family_id"])
            raise AppError(401, "UNAUTHENTICATED", "請重新登入")
        if rt["expires_at"] < dt.datetime.now(dt.timezone.utc):
            raise AppError(401, "UNAUTHENTICATED", "登入已過期，請重新登入")
        user = await repo.get_user(conn, rt["user_id"])
        if not user:
            raise AppError(401, "UNAUTHENTICATED", "請重新登入")
        await repo.revoke_refresh(conn, rt["id"])
        access = await _issue(conn, user, request, response, family_id=rt["family_id"])
    return {"access_token": access, "user": user_out(user)}


async def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        async with db.transaction() as conn:
            rt = await repo.get_refresh_for_update(conn, sha256(token))
            if rt:
                await repo.revoke_refresh(conn, rt["id"])
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
