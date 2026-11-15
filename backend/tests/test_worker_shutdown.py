"""worker 平滑關閉與安全重試：交還、逾時、收回孤兒、搶回後不覆蓋（需要本機 db＋redis：docker compose up -d db redis）。"""
import asyncio
import os
import signal
import uuid

import pytest
import pytest_asyncio

from app.core import db, redis
from app.core.config import settings
from app.workers import main, queue


@pytest_asyncio.fixture()
async def env(monkeypatch):
    # 其他測試的 TestClient 用的是另一個事件迴圈，連線物件不能共用
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(redis, "_client", None)
    monkeypatch.setattr(queue, "_arq", None)
    monkeypatch.setattr(main, "_stopping", False)
    # 用專屬的 arq 佇列，不會被本機其他 worker 拿走，也不會拿到別人的工作
    tag = uuid.uuid4().hex[:8]
    monkeypatch.setattr(queue, "QUEUES", {"interactive": f"arq:queue:test-i-{tag}", "default": f"arq:queue:test-d-{tag}"})
    await db.init_pool()
    yield
    await queue.close()
    await redis.close()
    await db.close_pool()


async def new_job(kind: str, **set_cols) -> int:
    async with db.connection() as conn:
        jid = await conn.fetchval(
            "INSERT INTO background_jobs (kind, queue, payload) VALUES ($1, 'interactive', $2) RETURNING id", kind, {})
        for col, val in set_cols.items():
            await conn.execute(f"UPDATE background_jobs SET {col} = $2 WHERE id = $1", jid, val)
    return jid


async def job(jid: int):
    async with db.connection() as conn:
        return await conn.fetchrow("SELECT *, run_at > now() + interval '5 seconds' AS deferred FROM background_jobs WHERE id = $1", jid)


async def until(cond, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not await cond():
        assert loop.time() < end, "timeout"
        await asyncio.sleep(0.1)


async def started(jid: int) -> bool:
    return (await job(jid))["status"] == "running"


@pytest.mark.asyncio
async def test_success(env, monkeypatch):
    async def ok(payload):
        pass
    monkeypatch.setitem(main.HANDLERS, "t_ok", ok)
    jid = await new_job("t_ok")
    assert await main.run_job({}, jid) == "ok"
    j = await job(jid)
    assert j["status"] == "succeeded" and j["attempts"] == 1


@pytest.mark.asyncio
async def test_cancel_on_shutdown_releases_without_using_an_attempt(env, monkeypatch):
    monkeypatch.setitem(main.HANDLERS, "t_hang", lambda payload: asyncio.Event().wait())
    jid = await new_job("t_hang")
    t = asyncio.create_task(main.run_job({}, jid))
    await until(lambda: started(jid))
    monkeypatch.setattr(main, "_stopping", True)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    j = await job(jid)
    assert (j["status"], j["attempts"], j["worker_id"], j["deferred"]) == ("retrying", 0, None, False)
    assert j["dispatched_at"] is not None          # 已經重新投遞，新的 worker 馬上能接


@pytest.mark.asyncio
async def test_cancel_by_job_timeout_is_a_failure_with_backoff(env, monkeypatch):
    monkeypatch.setitem(main.HANDLERS, "t_hang", lambda payload: asyncio.Event().wait())
    jid = await new_job("t_hang")
    t = asyncio.create_task(main.run_job({}, jid))
    await until(lambda: started(jid))
    t.cancel()                                      # 不是關閉中 → 等同 arq 的 job_timeout
    with pytest.raises(asyncio.CancelledError):
        await t
    j = await job(jid)
    assert (j["status"], j["attempts"], j["deferred"]) == ("retrying", 1, True)
    assert "job_timeout" in j["last_error"]


@pytest.mark.asyncio
async def test_finish_after_takeover_does_not_overwrite(env, monkeypatch):
    async def taken_over(payload):
        async with db.connection() as conn:         # 執行中被 sweep 收回、交給別人
            await conn.execute("UPDATE background_jobs SET status = 'retrying', worker_id = NULL WHERE id = $1", payload["id"])
    monkeypatch.setitem(main.HANDLERS, "t_takeover", taken_over)
    jid = await new_job("t_takeover")
    async with db.connection() as conn:
        await conn.execute("UPDATE background_jobs SET payload = $2 WHERE id = $1", jid, {"id": jid})
    assert await main.run_job({}, jid) == "ok"
    assert (await job(jid))["status"] == "retrying"


@pytest.mark.asyncio
async def test_sweep_reclaims_only_lost_workers(env):
    alive = f"{queue.HEARTBEAT_PREFIX}alive-{uuid.uuid4().hex[:6]}"
    await queue.heartbeat(alive)
    sql_ago = "now() - interval '1 minute'"
    ids = {}
    for name, wid in [("dead", f"{queue.HEARTBEAT_PREFIX}gone-{uuid.uuid4().hex[:6]}"), ("alive", alive), ("legacy", "old-worker:1")]:
        ids[name] = await new_job("t_x", status="running", worker_id=wid, attempts=1)
    ids["legacy_overdue"] = await new_job("t_x", status="running", worker_id="old-worker:2", attempts=1)
    async with db.connection() as conn:
        await conn.execute(f"UPDATE background_jobs SET started_at = {sql_ago} WHERE id = ANY($1::bigint[])", list(ids.values()))
        await conn.execute("UPDATE background_jobs SET started_at = now() - interval '20 minutes' WHERE id = $1", ids["legacy_overdue"])
    await queue.sweep()
    st = {k: (await job(v))["status"] for k, v in ids.items()}
    # 心跳不見的 → 收回；心跳還在的、舊版 worker（沒有心跳）未逾 15 分鐘的 → 不動
    assert st == {"dead": "retrying", "alive": "running", "legacy": "running", "legacy_overdue": "retrying"}
    assert (await job(ids["dead"]))["attempts"] == 1    # 硬殺仍算一次嘗試，避免把 worker 弄掛的工作無限重跑
    async with db.connection() as conn:
        await conn.execute("DELETE FROM background_jobs WHERE id = ANY($1::bigint[])", list(ids.values()))


@pytest.mark.asyncio
async def test_sigterm_drains_then_releases(env, monkeypatch):
    """真的跑 arq worker：SIGTERM 後做完短的工作、長的在等待時限到了交還，程序正常結束。"""
    async def fast(payload):
        await asyncio.sleep(1)
    monkeypatch.setitem(main.HANDLERS, "t_fast", fast)
    monkeypatch.setitem(main.HANDLERS, "t_slow", lambda payload: asyncio.Event().wait())
    monkeypatch.setattr(settings, "worker_drain_seconds", 3)
    async with queue.tx() as conn:
        fast_id = await queue.enqueue(conn, "t_fast", {}, queue="interactive")
        slow_id = await queue.enqueue(conn, "t_slow", {}, queue="interactive")
    run = asyncio.create_task(main.main(["interactive"]))
    await until(lambda: started(slow_id))
    await until(lambda: started(fast_id))
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(run, 15)
    await db.init_pool()                            # worker 關閉時把連線池關了
    f, s = await job(fast_id), await job(slow_id)
    assert f["status"] == "succeeded"
    assert (s["status"], s["attempts"], s["last_error"]) == ("retrying", 0, "released on worker shutdown")
    assert await redis.client().get(f"{redis.P}worker_alive:{main.WORKER_ID}") is None
