"""Redis：連線、限流、分散式鎖、冪等鍵、Pub/Sub（architecture.md §4.3）。

Redis 只放短命資料；Redis 不可用時限流 fail-open、冪等退回資料庫唯一約束。
"""
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import redis.asyncio as aioredis
import structlog

from app.core.config import settings
from app.core.errors import AppError

log = structlog.get_logger()
_client: aioredis.Redis | None = None
P = "ai:"


def client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------- 限流（固定視窗） ----------
LIMITS: dict[str, tuple[int, int]] = {          # action: (次數, 視窗秒數)
    "register": (10, 3600),
    "login": (30, 900) if not settings.is_dev else (300, 900),             # 每個 IP：擋換不同 Email 撞密碼（同一個 Email 另有失敗 5 次鎖 15 分鐘）
    "resume_upload": (20, 86400),
    "upload": (60, 3600),
    "target_create": (20, 3600),
    "question_gen": (20, 3600),
    "prep_gen": (20, 3600),
    "career_plan": (5, 3600) if not settings.is_dev else (50, 3600),
    "interview_create": (10, 86400) if not settings.is_dev else (200, 86400),
}
_mem: dict[str, tuple[int, float]] = {}


async def rate_limit(action: str, subject: str) -> None:
    limit, window = LIMITS[action]
    now = int(time.time())
    key = f"{P}rl:{action}:{subject}:{now // window}"
    try:
        n = await client().incr(key)
        if n == 1:
            await client().expire(key, window)
    except Exception:  # fail-open：改用程序內計數
        n, exp = _mem.get(key, (0, now + window))
        n += 1
        _mem[key] = (n, exp)
    if n > limit:
        retry = window - now % window
        raise AppError(429, "RATE_LIMITED", "操作太頻繁了，請稍後再試", {"retry_after": retry},
                       headers={"Retry-After": str(retry)})


async def login_failed(email_hash: str) -> int:
    key = f"{P}rl:login_fail:{email_hash}"
    try:
        n = await client().incr(key)
        if n == 1:
            await client().expire(key, 900)
        return n
    except Exception:
        return 0


async def login_fail_count(email_hash: str) -> int:
    try:
        return int(await client().get(f"{P}rl:login_fail:{email_hash}") or 0)
    except Exception:
        return 0


async def login_reset(email_hash: str) -> None:
    try:
        await client().delete(f"{P}rl:login_fail:{email_hash}")
    except Exception:
        pass


# ---------- 分散式鎖 ----------
_UNLOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


@asynccontextmanager
async def lock(name: str, ttl_sec: int = 60, *, raise_busy: AppError | None = None) -> AsyncIterator[bool]:
    key, token = f"{P}lock:{name}", uuid.uuid4().hex
    got = bool(await client().set(key, token, nx=True, ex=ttl_sec))
    if not got and raise_busy:
        raise raise_busy
    try:
        yield got
    finally:
        if got:
            try:
                await client().eval(_UNLOCK, 1, key, token)
            except Exception:
                pass


# ---------- 冪等鍵 ----------
async def idem_begin(user_id: str, key: str, fingerprint: str) -> dict | None:
    """回傳既有結果（done）或 None（取得處理權）。處理中 → 409；同 key 不同內容 → 422。"""
    rkey = f"{P}idem:{user_id}:{key}"
    try:
        ok = await client().set(rkey, json.dumps({"state": "processing", "fp": fingerprint}), nx=True, ex=86400)
        if ok:
            return None
        cur = json.loads(await client().get(rkey) or "{}")
    except AppError:
        raise
    except Exception:
        return None  # Redis 不可用：交給資料庫唯一約束
    if cur.get("fp") != fingerprint:
        raise AppError(422, "IDEMPOTENCY_KEY_REUSED", "這個請求代碼已用在不同的內容上")
    if cur.get("state") == "processing":
        raise AppError(409, "REQUEST_IN_PROGRESS", "上一個相同的請求還在處理中")
    return cur


async def idem_done(user_id: str, key: str, fingerprint: str, status: int, body: Any) -> None:
    try:
        await client().set(f"{P}idem:{user_id}:{key}",
                           json.dumps({"state": "done", "fp": fingerprint, "status": status, "body": body},
                                      default=str, ensure_ascii=False), ex=86400)
    except Exception:
        pass


async def idem_abort(user_id: str, key: str) -> None:
    try:
        await client().delete(f"{P}idem:{user_id}:{key}")
    except Exception:
        pass


# ---------- Pub/Sub ----------
async def publish(channel: str, event: str, data: dict) -> None:
    try:
        await client().publish(f"{P}evt:{channel}", json.dumps({"event": event, "data": data}, default=str))
    except Exception as e:
        log.warning("publish_failed", channel=channel, error=str(e))
