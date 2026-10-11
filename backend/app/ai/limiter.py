"""OpenAI 用量控管（architecture.md §9.4）：每個模型一個 Redis 信號量，面試中呼叫優先。

priority:
- "interview"：面試進行中（追問、快速轉錄、TTS）— 可用全部名額，最多等 2 秒
- "normal"   ：評分、報告、出題、建議 — 只能用一般名額（扣掉保留比例）
- "low"      ：職缺 ETL — 只用一半名額
Redis 不可用時 fail-open。
"""
import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.core import redis as r
from app.core.config import settings

_ACQUIRE = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[2]) then
  redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
  redis.call('EXPIRE', KEYS[1], 600)
  return 1
end
return 0
"""


class LimiterBusy(Exception):
    pass


def _cap(priority: str) -> int:
    total = settings.openai_concurrency
    if priority == "interview":
        return total
    if priority == "low":
        return max(1, total // 2)
    return max(1, int(total * (1 - settings.openai_reserved_ratio)))


@asynccontextmanager
async def slot(model: str, priority: str = "normal", hold_sec: int = 120) -> AsyncIterator[None]:
    key, token = f"{r.P}sem:openai:{model}", uuid.uuid4().hex
    max_wait = 2.0 if priority == "interview" else 120.0
    start, acquired = time.monotonic(), False
    try:
        while True:
            now = time.time()
            try:
                ok = await r.client().eval(_ACQUIRE, 1, key, now, _cap(priority), now + hold_sec, token)
            except Exception:
                break  # fail-open
            if ok:
                acquired = True
                break
            if time.monotonic() - start > max_wait:
                raise LimiterBusy(model)
            await asyncio.sleep(0.1)
        yield
    finally:
        if acquired:
            try:
                await r.client().zrem(key, token)
            except Exception:
                pass


async def live_interviews() -> int:
    """進行中面試數（依心跳），准入控制用。"""
    try:
        c = r.client()
        await c.zremrangebyscore(f"{r.P}live_interviews", "-inf", time.time() - 180)
        return await c.zcard(f"{r.P}live_interviews")
    except Exception:
        return 0


async def mark_live(session_id: str) -> None:
    try:
        await r.client().zadd(f"{r.P}live_interviews", {session_id: time.time()})
    except Exception:
        pass


async def unmark_live(session_id: str) -> None:
    try:
        await r.client().zrem(f"{r.P}live_interviews", session_id)
    except Exception:
        pass
