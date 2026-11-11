"""管理後台的進入門檻（網頁 /#admin 與 /admin/plans、/admin/users… 這些 API）。

三個條件都要符合：
1. 登入的帳號 Email 在 ADMIN_GOOGLE_EMAILS 名單內
2. 這次登入是用 Google（access token 的 amr = google；用密碼登入同一個帳號也不行）
3. 輸入後台帳號密碼（ADMIN_CONSOLE_USERNAME / ADMIN_CONSOLE_PASSWORD）→ 拿到短效的後台工作階段，
   之後每個後台 API 都要帶 X-Admin-Session 標頭

後台工作階段是另一種 JWT（typ = admin_console），綁定使用者，並帶後台帳密的指紋：
.env 換了帳密，舊的工作階段立刻失效。
"""
import datetime as dt
import hashlib
import hmac

import jwt
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core import db, redis
from app.core.config import settings
from app.core.errors import AppError
from app.core.security import CurrentUser, current_user

router = APIRouter(prefix="/admin/console", tags=["admin"])
HEADER = "X-Admin-Session"
TYP = "admin_console"
MAX_FAILS = 5                      # 帳密錯 5 次鎖 15 分鐘（沿用登入的失敗計數）


class ConsoleLogin(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=200)


def _fingerprint() -> str:
    raw = f"{settings.admin_console_username}\0{settings.admin_console_password}".encode()
    return hmac.new(settings.jwt_secret.encode(), raw, hashlib.sha256).hexdigest()[:24]


async def eligibility(user: CurrentUser) -> dict:
    """前兩個條件（名單、Google 登入）。回傳原因給前端顯示，不是給後端放行用。"""
    if not settings.admin_console_enabled:
        return {"eligible": False, "reason": "disabled"}
    async with db.connection() as conn:
        email = await conn.fetchval("SELECT email FROM users WHERE id = $1 AND status = 'active'", user.id)
    if not email or str(email).lower() not in settings.admin_emails:
        return {"eligible": False, "reason": "not_allowed"}
    if user.amr != "google":
        return {"eligible": False, "reason": "google_required"}
    return {"eligible": True, "reason": None}


async def _require_eligible(user: CurrentUser) -> None:
    e = await eligibility(user)
    if not e["eligible"]:
        # 不在名單的人一律回 404，不透露後台存在
        if e["reason"] in ("disabled", "not_allowed"):
            raise AppError(404, "NOT_FOUND", "找不到這個項目")
        raise AppError(403, "GOOGLE_LOGIN_REQUIRED", "請登出後改用 Google 登入，才能進入管理後台")


def _issue(user: CurrentUser) -> tuple[str, dt.datetime]:
    now = dt.datetime.now(dt.timezone.utc)
    exp = now + dt.timedelta(minutes=settings.admin_console_minutes)
    tok = jwt.encode({"sub": str(user.id), "typ": TYP, "fp": _fingerprint(), "iat": now, "exp": exp},
                     settings.jwt_secret, algorithm="HS256")
    return tok, exp


async def console_admin(request: Request, user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """後台 API 的依賴：三個條件都檢查（名單、Google 登入、有效的後台工作階段）。"""
    await _require_eligible(user)
    tok = request.headers.get(HEADER, "")
    try:
        data = jwt.decode(tok, settings.jwt_secret, algorithms=["HS256"]) if tok else None
    except jwt.PyJWTError:
        data = None
    if not data or data.get("typ") != TYP or data.get("sub") != str(user.id) \
            or not hmac.compare_digest(str(data.get("fp", "")), _fingerprint()):
        # 403 而不是 401：前端遇到 401 會去換發登入 token，這裡要的是重新輸入後台密碼
        raise AppError(403, "ADMIN_SESSION_REQUIRED", "請輸入後台帳號密碼")
    return user


@router.get("/status")
async def status(user: CurrentUser = Depends(current_user)):
    e = await eligibility(user)
    if e["reason"] in ("disabled", "not_allowed"):
        raise AppError(404, "NOT_FOUND", "找不到這個項目")
    return e | {"session_minutes": settings.admin_console_minutes}


@router.post("/login")
async def login(body: ConsoleLogin, request: Request, user: CurrentUser = Depends(current_user)):
    await _require_eligible(user)
    key = f"admin_console:{user.id}"
    if await redis.login_fail_count(key) >= MAX_FAILS:
        raise AppError(429, "RATE_LIMITED", "後台密碼錯太多次，請 15 分鐘後再試", headers={"Retry-After": "900"})
    ok_user = hmac.compare_digest(body.username.encode(), settings.admin_console_username.encode())
    ok_pw = hmac.compare_digest(body.password.encode(), settings.admin_console_password.encode())
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO app_events (user_id, event_name, request_id, properties) VALUES ($1, $2, $3, $4)",
            user.id, "admin.console_login" if ok_user and ok_pw else "admin.console_login_failed",
            getattr(request.state, "request_id", None), {})
    if not (ok_user and ok_pw):
        await redis.login_failed(key)
        raise AppError(400, "INVALID_CREDENTIALS", "後台帳號或密碼不正確")   # 不用 401：前端遇到 401 會自動換發 token 重送
    await redis.login_reset(key)
    tok, exp = _issue(user)
    return {"session": tok, "expires_at": exp}
