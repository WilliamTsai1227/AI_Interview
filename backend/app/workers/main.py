"""Worker：arq 取工作 → 依 background_jobs 執行 handler → 更新 outbox 狀態（重試／dead）。

開發：python -m app.workers.main            （同一個程序同時跑 interactive 與 default 兩條佇列）
正式：python -m app.workers.main interactive / default（分開部署、各自擴充）

平滑關閉（滾動更新）：收到 SIGTERM → 不再接新工作 → 等進行中的工作做完（最多 WORKER_DRAIN_SECONDS，預設 job_timeout 秒）→
還沒做完的取消並「交還」（狀態改回 retrying、不扣嘗試次數、立刻重新投遞），由新的 Pod 接手。
再收到一次訊號就不等了，直接交還（開發時按兩次 Ctrl+C）。
程序被硬殺（SIGKILL、OOM、斷電）時來不及交還：心跳 key 30 秒後過期，sweep 看到就把工作收回重排。
"""
import asyncio
import datetime as dt
import signal
import socket
import sys

import structlog
from arq import cron
from arq.worker import Worker

from app.ai.client import QuotaExceeded
from app.core import db, redis, storage
from app.core.config import settings
from app.core.migrate import ensure_partitions
from app.modules.career import service as career
from app.modules.interviews import live
from app.modules.interviews import service as interviews
from app.modules.jobs import insight as job_insight
from app.modules.jobs import service as jobs
from app.modules.preps import service as preps
from app.modules.questions import service as questions
from app.modules.reports import pipeline
from app.modules.resumes import service as resumes
from app.modules.revisions import service as revisions
from app.workers import queue

structlog.configure(processors=[structlog.processors.add_log_level, structlog.processors.TimeStamper(fmt="iso"),
                                structlog.processors.JSONRenderer(ensure_ascii=False)])
log = structlog.get_logger()
WORKER_ID = f"{queue.HEARTBEAT_PREFIX}{socket.gethostname()}:{id(object())}"
JOB_TIMEOUT = {"interactive": 300, "default": 600}      # 職涯規劃要先深入讀作品集，最久約 6 分鐘
_stopping = False       # 進入關閉流程後才取消的工作是「交還」，不是逾時失敗

HANDLERS = {
    "parse_resume": resumes.handle_parse,
    "parse_job": jobs.handle_parse_job,
    "job_insight": job_insight.handle,
    "generate_questions": questions.handle_generate,
    "build_rubric": questions.handle_build_rubric,
    "generate_prep": preps.handle_analyze,
    "extend_prep": preps.handle_extend,
    "build_career_plan": career.handle_build,
    "build_revision_cards": revisions.handle_cards,
    "build_revision": revisions.handle_build,
    "review_revision": revisions.handle_review,
    "synthesize_tts": interviews.handle_tts,
    "prepare_live": live.handle_prepare,
    "segment_live": live.handle_segment,
    "live_feedback": live.handle_feedback,
    "transcribe_attempt": pipeline.handle_transcribe,
    "evaluate_attempt": pipeline.handle_evaluate,
    "build_report": pipeline.handle_build_report,
}


async def run_job(ctx, job_id: int) -> str:
    async with db.connection() as conn:
        j = await conn.fetchrow(
            """UPDATE background_jobs SET status = 'running', attempts = attempts + 1, started_at = now(), worker_id = $2
                WHERE id = $1 AND status IN ('queued','retrying') RETURNING *""", job_id, WORKER_ID)
    if not j:
        return "skip"
    handler = HANDLERS.get(j["kind"])
    try:
        if not handler:
            raise RuntimeError(f"unknown job kind {j['kind']}")
        await handler(j["payload"])
    except asyncio.CancelledError:
        # 關閉時被取消 → 交還給其他 worker；不是關閉 → arq 的 job_timeout 到了，當成一般失敗
        await (_release(j) if _stopping else _fail(j, TimeoutError(f"{j['kind']} exceeded job_timeout")))
        raise
    except Exception as e:
        await _fail(j, e)
        return "failed"
    async with db.connection() as conn:
        # 只在這筆還是自己的時候才收尾：被 sweep 收回、已經交給別人重跑的，不要蓋掉對方的狀態
        done = await conn.execute(
            """UPDATE background_jobs SET status = 'succeeded', finished_at = now(), last_error = NULL
                WHERE id = $1 AND status = 'running' AND worker_id = $2""", job_id, WORKER_ID)
    if done == "UPDATE 0":
        log.warning("job_finished_after_takeover", job_id=job_id, kind=j["kind"])
    return "ok"


async def _fail(j, e: BaseException) -> None:
    log.warning("job_failed", job_id=j["id"], kind=j["kind"], attempt=j["attempts"], error=repr(e))
    # 額度用完重試也沒用，直接結束
    dead = j["attempts"] >= j["max_attempts"] or isinstance(e, QuotaExceeded)
    delay = min(300, 2 ** j["attempts"] * 5)
    async with db.connection() as conn:
        n = await conn.execute(
            """UPDATE background_jobs SET status = $2, last_error = $3, run_at = now() + make_interval(secs => $4),
                      finished_at = CASE WHEN $2 = 'dead' THEN now() END, worker_id = NULL
                WHERE id = $1 AND status = 'running' AND worker_id = $5""",
            j["id"], "dead" if dead else "retrying", repr(e)[:1000], delay, WORKER_ID)
    if not dead and n != "UPDATE 0":
        await queue.dispatch(j["id"], j["kind"], j["queue"], dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay))


async def _release(j) -> None:
    """關閉時交還做到一半的工作：不扣嘗試次數，立刻重新投遞（handler 都可以安全重跑）。"""
    log.info("job_released", job_id=j["id"], kind=j["kind"])
    async with db.connection() as conn:
        n = await conn.execute(
            """UPDATE background_jobs SET status = 'retrying', attempts = attempts - 1, worker_id = NULL, run_at = now(),
                      last_error = 'released on worker shutdown'
                WHERE id = $1 AND status = 'running' AND worker_id = $2""", j["id"], WORKER_ID)
    if n != "UPDATE 0":
        await queue.dispatch(j["id"], j["kind"], j["queue"])


# ---------- 排程 ----------
async def sweep_outbox(ctx):
    n = await queue.sweep()
    if n:
        log.info("sweep", redispatched=n)


async def pause_lost(ctx):
    await interviews.pause_lost_sessions()


async def expire_paused(ctx):
    await interviews.expire_paused_sessions()


async def daily_maintenance(ctx):
    async with db.connection() as conn:
        await ensure_partitions(conn)
        await conn.execute("DELETE FROM background_jobs WHERE status = 'succeeded' AND finished_at < now() - interval '30 days'")
        # 沒被使用的上傳物件（1 小時）、被重錄丟掉的錄音（24 小時）
        tickets = await conn.fetch(
            """UPDATE upload_tickets SET status = 'expired' WHERE status IN ('issued','uploaded') AND expires_at < now()
               RETURNING object_key""")
        discarded = await conn.fetch(
            """UPDATE answer_attempts SET audio_key = NULL WHERE status IN ('discarded','interrupted') AND audio_key IS NOT NULL
                  AND updated_at < now() - interval '24 hours' RETURNING audio_key""")
        old_audio = await conn.fetch(
            """UPDATE answer_attempts SET audio_key = NULL WHERE audio_key IS NOT NULL AND created_at < now() - interval '180 days'
               RETURNING audio_key""")
    keys = [r[0] for r in tickets] + [r[0] for r in discarded] + [r[0] for r in old_audio]
    for i in range(0, len(keys), 500):
        try:
            await storage.delete(*keys[i:i + 500])
        except Exception as e:
            log.warning("purge_failed", error=str(e))


async def startup(ctx):
    await db.init_pool()


async def shutdown(ctx):
    await queue.close()
    await redis.close()
    await db.close_pool()


def make_worker(name: str, with_cron: bool) -> Worker:
    crons = [cron(sweep_outbox, second={0, 15, 30, 45}, run_at_startup=True),
             cron(pause_lost, second={5}),
             cron(expire_paused, minute=set(range(0, 60, 10)), second={10}),
             cron(daily_maintenance, hour={3}, minute={30}, run_at_startup=True)] if with_cron else []
    return Worker(functions=[run_job], cron_jobs=crons, queue_name=queue.QUEUES[name],
                  redis_settings=queue.redis_settings(), on_startup=startup, on_shutdown=shutdown,
                  max_jobs=8 if name == "interactive" else 6, keep_result=60, job_timeout=JOB_TIMEOUT[name],
                  handle_signals=False)       # 訊號由 main() 統一處理：同一個程序可能跑兩條佇列


async def _heartbeat() -> None:
    """每 10 秒續一次「我還活著」，sweep 靠它分辨工作是在跑還是 worker 已經不在了。"""
    while True:
        try:
            await queue.heartbeat(WORKER_ID)
        except Exception as e:
            log.warning("heartbeat_failed", error=str(e))
        await asyncio.sleep(10)


async def _drain(workers: list[Worker], wait_s: int, force: asyncio.Event) -> None:
    global _stopping
    for w in workers:
        w.allow_pick_jobs = False

    def running() -> list[asyncio.Task]:
        return [t for w in workers for t in w.tasks.values() if not t.done()]

    log.info("worker_draining", running=len(running()), wait_s=wait_s)
    deadline = asyncio.get_running_loop().time() + wait_s
    while running() and not force.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.5)
    _stopping = True
    left = running()
    for t in left:
        t.cancel()
    await asyncio.gather(*left, return_exceptions=True)
    log.info("worker_drained", released=len(left))


async def main(which: list[str]) -> None:
    workers = [make_worker(n, with_cron=(n == "default")) for n in which]
    stop, force = asyncio.Event(), asyncio.Event()

    def on_signal() -> None:
        (force if stop.is_set() else stop).set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal)
    log.info("workers_starting", queues=which, worker_id=WORKER_ID)
    hb = asyncio.create_task(_heartbeat())
    runs = [asyncio.create_task(w.async_run()) for w in workers]
    stopped = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait([stopped, *runs], return_when=asyncio.FIRST_COMPLETED)
        if stop.is_set():
            await _drain(workers, settings.worker_drain_seconds or max(JOB_TIMEOUT[n] for n in which), force)
    finally:
        for w in workers:
            if w.main_task:
                w.main_task.cancel()
        await asyncio.gather(*runs, return_exceptions=True)
        stopped.cancel()
        hb.cancel()
        try:
            await queue.heartbeat(WORKER_ID, alive=False)
        except Exception:
            pass
        for w in workers:
            await w.close()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        log.info("workers_stopped")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["interactive", "default"]))
