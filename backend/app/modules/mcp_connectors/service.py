"""MCP 連接器：使用者授權自己的 MCP 帳號（目前只有 Notion），token 加密存放、過期前自動刷新。

授權流程（比照 auth/google.py）：
  POST /mcp-connectors/{p}/start（要登入）→ 產生 state 與 PKCE verifier，連同 user_id 存 Redis 10 分鐘，
  state 另外綁在瀏覽器 Cookie → 回傳授權網址，前端整頁導過去
  → 使用者同意 → GET /mcp-connectors/{p}/callback：驗證 state（Redis 一次性＋Cookie）、用授權碼換 token、
  加密存進 mcp_connections → 302 回前端。
callback 是跨站導回來的頂層 GET，沒有 Authorization 標頭；使用者身分只認 start 時寫進 Redis 的 user_id。

token 一律不出後端：API 只回連線狀態。加密時把 user_id:provider 當 AAD，密文搬到別的列會解不開。
"""
import base64
import datetime as dt
import hashlib
import json
import secrets
from urllib.parse import urlencode
from uuid import UUID

import httpx
import structlog
from fastapi import Request, Response
from fastapi.responses import RedirectResponse

from app.core import crypto, db, redis
from app.core.config import settings
from app.core.errors import AppError, not_found
from app.modules.mcp_connectors import oauth, providers
from app.modules.mcp_connectors.providers import Provider

log = structlog.get_logger()
STATE_COOKIE = "mcp_oauth_state"
STATE_PATH = "/api/v1/mcp-connectors"
STATE_TTL = 600
REFRESH_MARGIN = dt.timedelta(seconds=60)

ERRORS = {"cancelled", "state", "failed"}       # 前端依代碼顯示訊息


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _aad(user_id: UUID, provider: str) -> bytes:
    return f"{user_id}:{provider}".encode()


def _provider(key: str) -> Provider:
    if not settings.mcp_connectors_enabled:
        raise AppError(404, "NOT_FOUND", "尚未開放 MCP 連接器")
    p = providers.get(key)
    if not p:
        raise not_found("不支援這個連接器")
    return p


# ---------- OAuth client（DCR 結果，全站共用） ----------
async def _client(p: Provider) -> dict:
    """回傳 {client_id, client_secret, meta}。沒有註冊過、或 redirect_uri 換了（BASE_URL 改了）就重新註冊。"""
    redirect_uri = settings.mcp_callback(p.key)
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT * FROM mcp_oauth_clients WHERE provider = $1", p.key)
    if row and row["metadata"].get("redirect_uri") == redirect_uri:
        return _client_out(row)
    meta = await oauth.discover(p.server_url)
    reg = await oauth.register(meta, redirect_uri)
    secret = crypto.encrypt(reg["client_secret"], p.key.encode()) if reg.get("client_secret") else None
    md = {"server": meta, "redirect_uri": redirect_uri}
    async with db.transaction() as conn:
        # 同時有兩個人第一次連接：先寫入的贏，另一個改用已存的那份（多註冊一次無害）
        row = await conn.fetchrow(
            """INSERT INTO mcp_oauth_clients (provider, client_id, client_secret, metadata) VALUES ($1, $2, $3, $4)
               ON CONFLICT (provider) DO UPDATE SET client_id = EXCLUDED.client_id, client_secret = EXCLUDED.client_secret,
                 metadata = EXCLUDED.metadata, created_at = now()
               WHERE mcp_oauth_clients.metadata->>'redirect_uri' IS DISTINCT FROM EXCLUDED.metadata->>'redirect_uri'
               RETURNING *""", p.key, reg["client_id"], secret, md)
        if row is None:
            row = await conn.fetchrow("SELECT * FROM mcp_oauth_clients WHERE provider = $1", p.key)
    log.info("mcp_client_registered", provider=p.key)
    return _client_out(row)


def _client_out(row) -> dict:
    secret = crypto.decrypt(row["client_secret"], row["provider"].encode()) if row["client_secret"] else None
    return {"client_id": row["client_id"], "client_secret": secret, "meta": row["metadata"]["server"]}


# ---------- 列表 ----------
async def list_for(user_id: UUID) -> dict:
    if not settings.mcp_connectors_enabled:
        return {"enabled": False, "items": []}
    async with db.connection() as conn:
        rows = {r["provider"]: r for r in await conn.fetch(
            "SELECT provider, account_label, status, created_at FROM mcp_connections WHERE user_id = $1", user_id)}
    items = []
    for p in providers.all_():
        r = rows.get(p.key)
        items.append({"provider": p.key, "name": p.name, "connected": r is not None,
                      "status": r["status"] if r else None, "account_label": r["account_label"] if r else None,
                      "connected_at": r["created_at"] if r else None})
    return {"enabled": True, "items": items}


# ---------- 授權 ----------
async def start(user_id: UUID, key: str, response: Response) -> dict:
    p = _provider(key)
    await redis.rate_limit("mcp_connect", str(user_id))
    try:
        c = await _client(p)
    except (oauth.OAuthError, httpx.HTTPError) as e:
        log.warning("mcp_client_setup_failed", provider=key, error=repr(e)[:300])
        raise AppError(502, "MCP_UNAVAILABLE", f"暫時連不上 {p.name}，請稍後再試")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    await redis.client().set(f"{redis.P}oauth:mcp:{state}", json.dumps(
        {"user_id": str(user_id), "provider": key, "verifier": verifier, "client_id": c["client_id"]}), ex=STATE_TTL)
    params = {"response_type": "code", "client_id": c["client_id"], "redirect_uri": settings.mcp_callback(key),
              "state": state, "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
              "code_challenge_method": "S256", "resource": p.server_url}
    # 授權伺服器導回來是跨站的頂層 GET，Cookie 必須是 Lax
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", path=STATE_PATH)
    return {"authorize_url": f"{c['meta']['authorization_endpoint']}?{urlencode(params)}"}


def _back(key: str | None = None, error: str | None = None) -> RedirectResponse:
    q = urlencode({"mcp_error": error} if error else {"mcp_connected": key})
    resp = RedirectResponse(f"/?{q}#new", status_code=302)
    resp.delete_cookie(STATE_COOKIE, path=STATE_PATH)
    return resp


async def callback(key: str, request: Request) -> RedirectResponse:
    p = _provider(key)
    q = request.query_params
    if q.get("error"):
        return _back(error="cancelled" if q.get("error") == "access_denied" else "failed")
    state, code = q.get("state"), q.get("code")
    cookie_state = request.cookies.get(STATE_COOKIE)
    if not state or not code or not cookie_state or not secrets.compare_digest(state, cookie_state):
        return _back(error="state")
    raw = await redis.client().getdel(f"{redis.P}oauth:mcp:{state}")     # 一次性
    if not raw:
        return _back(error="state")
    st = json.loads(raw)
    if st["provider"] != key:
        return _back(error="state")
    user_id = UUID(st["user_id"])
    try:
        c = await _client(p)
        if c["client_id"] != st["client_id"]:          # 授權途中 client 被重新註冊
            return _back(error="state")
        tok = await oauth.exchange(c["meta"], code=code, verifier=st["verifier"], redirect_uri=settings.mcp_callback(key),
                                   resource=p.server_url, client_id=c["client_id"], client_secret=c["client_secret"])
    except Exception as e:
        detail = e.response.text[:300] if isinstance(e, httpx.HTTPStatusError) else repr(e)[:300]
        log.warning("mcp_exchange_failed", provider=key, error=detail)
        return _back(error="failed")
    async with db.transaction() as conn:
        if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM users WHERE id = $1 AND status = 'active')", user_id):
            return _back(error="failed")
        await _save(conn, user_id, key, tok, label=_label(tok))
    log.info("mcp_connected", provider=key, user_id=str(user_id))
    return _back(key)


def _label(tok: dict) -> str | None:
    ws = tok.get("workspace")
    label = tok.get("workspace_name") or (ws.get("name") if isinstance(ws, dict) else None)
    return str(label)[:100] if label else None


def _expires(tok: dict) -> dt.datetime | None:
    try:
        return dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(tok["expires_in"]))
    except (KeyError, TypeError, ValueError):
        return None


async def _save(conn: db.Connection, user_id: UUID, key: str, tok: dict, *, label: str | None = None,
                keep_refresh: bytes | None = None) -> None:
    aad = _aad(user_id, key)
    access = crypto.encrypt(tok["access_token"], aad)
    refresh = crypto.encrypt(tok["refresh_token"], aad) if tok.get("refresh_token") else keep_refresh
    await conn.execute(
        """INSERT INTO mcp_connections (user_id, provider, account_label, access_token, refresh_token, key_version, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT (user_id, provider) DO UPDATE SET
             account_label = COALESCE(EXCLUDED.account_label, mcp_connections.account_label),
             access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token,
             key_version = EXCLUDED.key_version, expires_at = EXCLUDED.expires_at, status = 'active', updated_at = now()""",
        user_id, key, label, access, refresh, crypto.KEY_VERSION, _expires(tok))


# ---------- 中斷連線 ----------
async def disconnect(user_id: UUID, key: str) -> None:
    p = _provider(key)
    async with db.transaction() as conn:
        row = await conn.fetchrow("DELETE FROM mcp_connections WHERE user_id = $1 AND provider = $2 RETURNING *", user_id, key)
    if not row:
        return
    try:                                   # 盡力撤銷；我們這邊的 token 已經刪掉了
        c = await _client(p)
        token = crypto.decrypt(row["refresh_token"] or row["access_token"], _aad(user_id, key))
        await oauth.revoke(c["meta"], token=token, client_id=c["client_id"], client_secret=c["client_secret"])
    except Exception as e:
        log.info("mcp_revoke_failed", provider=key, error=repr(e)[:200])


# ---------- 給 AI 層用：取得可用的 access token ----------
async def access_token(user_id: UUID, key: str) -> str:
    """回傳有效的 access token；快過期就先用 refresh token 換新。沒連接或刷新失敗 → 409 MCP_NOT_CONNECTED。"""
    p = _provider(key)
    row = await _row(user_id, key)
    if _fresh(row):
        return crypto.decrypt(row["access_token"], _aad(user_id, key))
    async with redis.lock(f"mcp:refresh:{user_id}:{key}", 30) as got:
        if not got:                        # 別的請求正在刷新：重讀一次
            row = await _row(user_id, key)
            if _fresh(row):
                return crypto.decrypt(row["access_token"], _aad(user_id, key))
            raise AppError(409, "MCP_BUSY", f"{p.name} 連線正在更新，請稍後再試")
        row = await _row(user_id, key)
        if _fresh(row):
            return crypto.decrypt(row["access_token"], _aad(user_id, key))
        if not row["refresh_token"]:
            await _mark_error(user_id, key)
            raise _not_connected(p)
        try:
            c = await _client(p)
            tok = await oauth.refresh(c["meta"], refresh_token=crypto.decrypt(row["refresh_token"], _aad(user_id, key)),
                                      resource=p.server_url, client_id=c["client_id"], client_secret=c["client_secret"])
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:       # refresh token 失效或被撤銷：要使用者重新連接
                log.info("mcp_refresh_rejected", provider=key, status=e.response.status_code)
                await _mark_error(user_id, key)
                raise _not_connected(p)
            raise AppError(502, "MCP_UNAVAILABLE", f"暫時連不上 {p.name}，請稍後再試")
        except (oauth.OAuthError, httpx.HTTPError):
            raise AppError(502, "MCP_UNAVAILABLE", f"暫時連不上 {p.name}，請稍後再試")
        async with db.transaction() as conn:
            await _save(conn, user_id, key, tok, keep_refresh=row["refresh_token"])
        return tok["access_token"]


async def _row(user_id: UUID, key: str):
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT * FROM mcp_connections WHERE user_id = $1 AND provider = $2", user_id, key)
    if not row or row["status"] != "active":
        raise _not_connected(providers.get(key))
    return row


def _fresh(row) -> bool:
    return row["expires_at"] is None or row["expires_at"] - dt.datetime.now(dt.timezone.utc) > REFRESH_MARGIN


async def _mark_error(user_id: UUID, key: str) -> None:
    async with db.transaction() as conn:
        await conn.execute("UPDATE mcp_connections SET status = 'error', updated_at = now() WHERE user_id = $1 AND provider = $2",
                           user_id, key)


def _not_connected(p: Provider) -> AppError:
    return AppError(409, "MCP_NOT_CONNECTED", f"請先到「設定 → MCP 連接器」連接 {p.name}", {"provider": p.key})
