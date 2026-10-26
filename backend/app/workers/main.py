"""Worker：arq 取工作 → 依 background_jobs 執行 handler → 更新 outbox 狀態（重試／dead）。

開發：python -m app.workers.main            （同一個程序同時跑 interactive 與 default 兩條佇列）
正式：python -m app.workers.main interactive / default（分開部署、各自擴充）
"""
import asyncio
import datetime as dt
import socket
import sys

import structlog
from arq import cron
from arq.worker import Worker

from app.core import db, redis, storage
from app.core.migrate import ensure_partitions
from app.modules.career import service as career
from app.modules.interviews import service as interviews
from app.modules.jobs import service as jobs
from app.modules.preps import service as preps
from app.modules.questions import service as questions
from app.modules.reports import pipeline
from app.modules.resumes import service as resumes
from app.workers import queue

structlog.configure(processors=[structlog.processors.add_log_level, structlog.processors.TimeStamper(fmt="iso"),
                                structlog.processors.JSONRenderer(ensure_ascii=False)])
log = structlog.get_logger()
WORKER_ID = f"{socket.gethostname()}:{id(object())}"

HANDLERS = {
    "parse_resume": resumes.handle_parse,
    "parse_job": jobs.handle_parse_job,
    "generate_questions": questions.handle_generate,
    "build_rubric": questions.handle_build_rubric,
    "generate_prep": preps.handle_analyze,
    "extend_prep": preps.handle_extend,
    "build_career_plan": career.handle_build,
    "synthesize_tts": interviews.handle_tts,
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
    except Exception as e:
        log.warning("job_failed", job_id=job_id, kind=j["kind"], attempt=j["attempts"], error=repr(e))
        dead = j["attempts"] >= j["max_attempts"]
        delay = min(300, 2 ** j["attempts"] * 5)
        async with db.connection() as conn:
            await conn.execute(
                """UPDATE background_jobs SET status = $2, last_error = $3, run_at = now() + make_interval(secs => $4),
                          finished_at = CASE WHEN $2 = 'dead' THEN now() END WHERE id = $1""",
                job_id, "dead" if dead else "retrying", repr(e)[:1000], delay)
        if not dead:
            await queue.dispatch(job_id, j["kind"], j["queue"], dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay))
        return "failed"
    async with db.connection() as conn:
        await conn.execute("UPDATE background_jobs SET status = 'succeeded', finished_at = now(), last_error = NULL WHERE id = $1", job_id)
    return "ok"


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
                  max_jobs=8 if name == "interactive" else 6, job_timeout=300, keep_result=60,
                  handle_signals=False)


async def main(which: list[str]) -> None:
    workers = [make_worker(n, with_cron=(n == "default")) for n in which]
    log.info("workers_starting", queues=which)
    try:
        await asyncio.gather(*(w.async_run() for w in workers))
    finally:
        for w in workers:
            await w.close()


if __name__ == "__main__":
    which = sys.argv[1:] or ["interactive", "default"]
    try:
        asyncio.run(main(which))
    except KeyboardInterrupt:
        pass
