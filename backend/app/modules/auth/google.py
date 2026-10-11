"""Google 登入：OAuth 2.0 授權碼流程＋PKCE＋state（api.md §3）。

流程：/auth/google/start 產生 state 與 code_verifier（存 Redis 10 分鐘，state 另外綁在瀏覽器 Cookie）
→ Google 同意畫面 → /auth/google/callback 驗證 state、用授權碼換 token、讀使用者資料
→ 依規則找到或建立帳號 → 發一般登入相同的 refresh Cookie → 302 回前端。

帳號連結規則（link_or_create）：
1. 已連結過這個 Google 帳號（provider_user_id = sub）→ 直接登入。
2. 同一個 Email 已用密碼註冊 → 連結到該帳號（Google 已驗證這個 Email 屬於本人）。
   若該帳號的 Email 從未驗證過，先清除密碼並登出所有裝置，
   避免「有人先用別人的 Email 註冊、等本人用 Google 登入後共用帳號」的搶先註冊攻擊。
3. 都沒有 → 建立新帳號（沒有密碼，email_verified_at = now）。
Google 回傳 email_verified = false 時拒絕登入。
"""
import base64
import hashlib
import json
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse

from app.core import db, redis
from app.core.config import settings
from app.core.errors import AppError
from app.modules.auth import repository as repo
from app.modules.auth import service

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
STATE_COOKIE = "oauth_state"
STATE_PATH = "/api/v1/auth/google"
STATE_TTL = 600

ERRORS = {
    "cancelled": "已取消 Google 登入",
    "state": "登入逾時或連結已失效，請再試一次",
    "unverified": "這個 Google 帳號的 Email 尚未驗證，無法登入",
    "failed": "Google 登入失敗，請再試一次",
    "disabled": "這個帳號已停用",
}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _fail(code: str) -> RedirectResponse:
    resp = RedirectResponse(f"/?auth_error={code}", status_code=302)
    resp.delete_cookie(STATE_COOKIE, path=STATE_PATH)
    return resp


async def start(request: Request) -> RedirectResponse:
    if not settings.google_enabled:
        raise AppError(404, "NOT_FOUND", "尚未開放 Google 登入")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    await redis.client().set(f"{redis.P}oauth:google:{state}", json.dumps({"verifier": verifier}), ex=STATE_TTL)
    params = {"client_id": settings.google_client_id, "redirect_uri": settings.google_callback,
              "response_type": "code", "scope": "openid email profile", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
              "access_type": "online"}
    resp = RedirectResponse(f"{AUTH_URL}?{urlencode(params)}", status_code=302)
    # Google 導回來是跨站的頂層 GET，Cookie 必須是 Lax（Strict 不會被送回來）
    resp.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, httponly=True, secure=settings.cookie_secure,
                    samesite="lax", path=STATE_PATH)
    return resp


async def _exchange(code: str, verifier: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(TOKEN_URL, data={"code": code, "client_id": settings.google_client_id,
                                          "client_secret": settings.google_client_secret,
                                          "redirect_uri": settings.google_callback,
                                          "grant_type": "authorization_code", "code_verifier": verifier})
        r.raise_for_status()
        token = r.json()["access_token"]
        u = await c.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"})
        u.raise_for_status()
        return u.json()


async def callback(request: Request) -> RedirectResponse:
    q = request.query_params
    if q.get("error"):
        return _fail("cancelled" if q.get("error") == "access_denied" else "failed")
    state, code = q.get("state"), q.get("code")
    cookie_state = request.cookies.get(STATE_COOKIE)
    if not state or not code or not cookie_state or not secrets.compare_digest(state, cookie_state):
        return _fail("state")
    raw = await redis.client().getdel(f"{redis.P}oauth:google:{state}")     # 一次性
    if not raw:
        return _fail("state")
    try:
        info = await _exchange(code, json.loads(raw)["verifier"])
    except Exception:
        return _fail("failed")
    if not info.get("sub") or not info.get("email"):
        return _fail("failed")
    if not info.get("email_verified"):
        return _fail("unverified")

    resp = RedirectResponse("/#new", status_code=302)
    resp.delete_cookie(STATE_COOKIE, path=STATE_PATH)
    async with db.transaction() as conn:
        user = await link_or_create(conn, info["sub"], info["email"].lower(), info.get("name") or "")
        if user is None:
            return _fail("disabled")
        await repo.touch_login(conn, user["id"])
        await service._issue(conn, user, request, resp)
        await service._event(conn, "auth.google_login", user["id"], request)
    return resp


async def link_or_create(conn: db.Connection, sub: str, email: str, name: str) -> db.Record | None:
    ident = await conn.fetchrow(
        "SELECT user_id FROM oauth_identities WHERE provider = 'google' AND provider_user_id = $1", sub)
    if ident:
        u = await conn.fetchrow("SELECT * FROM users WHERE id = $1", ident["user_id"])
        return u if u and u["status"] == "active" else None
    u = await conn.fetchrow("SELECT * FROM users WHERE email = $1 AND status <> 'deleted' FOR UPDATE", email)
    if u:
        if u["status"] != "active":
            return None
        if u["email_verified_at"] is None:      # 防搶先註冊：清掉未驗證帳號的密碼並登出所有裝置
            await conn.execute("UPDATE users SET password_hash = NULL, email_verified_at = now() WHERE id = $1", u["id"])
            await repo.revoke_all_for_user(conn, u["id"])
    else:
        u = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, display_name, email_verified_at)
               VALUES ($1, NULL, $2, now()) RETURNING *""", email, (name.strip() or email.split("@")[0])[:60])
    await conn.execute(
        "INSERT INTO oauth_identities (user_id, provider, provider_user_id) VALUES ($1, 'google', $2) ON CONFLICT DO NOTHING",
        u["id"], sub)
    return await conn.fetchrow("SELECT * FROM users WHERE id = $1", u["id"])
