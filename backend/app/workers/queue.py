"""工作投遞：Transactional outbox（background_jobs）＋ arq（architecture.md §4.3.3）。

用法：
    async with tx() as conn:
        ...業務寫入...
        await enqueue(conn, "parse_resume", {"resume_id": str(rid)})
    # 交易 commit 後自動投遞到 Redis；投遞失敗由 sweeper 補投
"""
import datetime as dt
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core import db, redis
from app.core.config import settings

log = structlog.get_logger()
QUEUES = {"interactive": "arq:queue:interactive", "default": "arq:queue:default"}
HEARTBEAT_TTL = 30          # worker 每 10 秒續一次；超過 30 秒沒續就當作已經不在了
# 有心跳的 worker 的 ID 都以這個開頭。滾動更新時舊版 worker 還在跑、但它不會續心跳，
# 沒有這個前綴的工作只能等 15 分鐘的保底規則，免得新版 sweep 把舊版正在做的工作搶走
HEARTBEAT_PREFIX = "hb:"
_pending: ContextVar[list | None] = ContextVar("_pending_jobs", default=None)
_arq: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def arq_pool() -> ArqRedis:
    global _arq
    if _arq is None:
        _arq = await create_pool(redis_settings())
    return _arq


async def close() -> None:
    global _arq
    if _arq is not None:
        await _arq.aclose()
        _arq = None


async def enqueue(conn: db.Connection, kind: str, payload: dict, *, queue: str = "default",
                  dedupe_key: str | None = None, run_at: dt.datetime | None = None,
                  max_attempts: int = 5) -> int | None:
    """在呼叫端的交易中寫入 outbox。dedupe_key 已有進行中的工作時回傳 None。"""
    job_id = await conn.fetchval(
        """
        INSERT INTO background_jobs (kind, queue, payload, dedupe_key, run_at, max_attempts)
        VALUES ($1, $2, $3, $4, COALESCE($5, now()), $6)
        ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running','retrying')
        DO NOTHING
        RETURNING id
        """,
        kind, queue, payload, dedupe_key, run_at, max_attempts,
    )
    pend = _pending.get()
    if job_id is not None and pend is not None:
        pend.append((job_id, kind, queue, run_at))
    return job_id


async def dispatch(job_id: int, kind: str, queue: str, run_at: dt.datetime | None = None) -> None:
    try:
        pool = await arq_pool()
        await pool.enqueue_job("run_job", job_id, _job_id=f"bg:{job_id}:{dt.datetime.now().timestamp():.0f}",
                               _queue_name=QUEUES[queue], _defer_until=run_at)
        async with db.connection() as conn:
            await conn.execute("UPDATE background_jobs SET dispatched_at = now() WHERE id = $1", job_id)
    except Exception as e:  # 交給 sweeper
        log.warning("dispatch_failed", job_id=job_id, kind=kind, error=str(e))


@asynccontextmanager
async def tx() -> AsyncIterator[db.Connection]:
    """交易＋commit 後投遞這個交易中排入的工作。"""
    pend: list = []
    token = _pending.set(pend)
    try:
        async with db.transaction() as conn:
            yield conn
    finally:
        _pending.reset(token)
    for job_id, kind, queue, run_at in pend:
        await dispatch(job_id, kind, queue, run_at)


def _alive_key(worker_id: str) -> str:
    return f"{redis.P}worker_alive:{worker_id}"


async def heartbeat(worker_id: str, alive: bool = True) -> None:
    if alive:
        await redis.client().set(_alive_key(worker_id), "1", ex=HEARTBEAT_TTL)
    else:
        await redis.client().delete(_alive_key(worker_id))


async def sweep() -> int:
    """收回孤兒工作，再重新投遞尚未投遞、或投遞後在 Redis 遺失的工作。

    孤兒：狀態是 running，但執行它的 worker 心跳已經消失（被硬殺、OOM），30～45 秒就會收回；
    保險起見，執行超過 15 分鐘（比最長的 job_timeout 還久）的也一律收回。
    收回會保留已用掉的嘗試次數：同一個工作一直把 worker 弄掛時，最後會變成 dead，而不是無限重跑。
    """
    async with db.connection() as conn:
        running = await conn.fetch(
            """SELECT id, worker_id, started_at < now() - interval '15 minutes' AS overdue FROM background_jobs
                WHERE status = 'running' AND started_at < now() - make_interval(secs => $1)""", HEARTBEAT_TTL)
    orphans = [r["id"] for r in running if r["overdue"] or not r["worker_id"]]
    rest = [r for r in running if r["id"] not in orphans and r["worker_id"].startswith(HEARTBEAT_PREFIX)]
    if rest:
        alive = await redis.client().mget([_alive_key(r["worker_id"]) for r in rest])
        orphans += [r["id"] for r, a in zip(rest, alive) if a is None]
    async with db.connection() as conn:
        stuck = await conn.fetch(
            """
            UPDATE background_jobs SET status = 'retrying', worker_id = NULL, run_at = now(), dispatched_at = NULL,
                   last_error = 'worker lost'
             WHERE id = ANY($1::bigint[]) AND status = 'running'
             RETURNING id
            """, orphans) if orphans else []
        rs = await conn.fetch(
            """
            SELECT id, kind, queue, run_at FROM background_jobs
             WHERE status IN ('queued','retrying') AND run_at <= now()
               AND (dispatched_at IS NULL OR dispatched_at < now() - interval '2 minutes')
             ORDER BY run_at LIMIT 200
            """)
    if stuck:
        log.warning("sweep_reclaimed", job_ids=[r["id"] for r in stuck])
    for r in rs:
        await dispatch(r["id"], r["kind"], r["queue"], None)
    return len(rs) + len(stuck)
