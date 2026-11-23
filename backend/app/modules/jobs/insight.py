"""職缺解讀（job_insights）：排程、背景產生、輸出。跟職缺走，同一個職缺所有使用者共用一份。"""
from uuid import UUID

from app.ai.agents import insight as ai_insight
from app.core import db
from app.workers.queue import enqueue, tx


async def start(conn: db.Connection, user_id: UUID, job_post_id: UUID, *, force: bool = False) -> None:
    """還沒有解讀（或 force：職缺內容改了／上次失敗）時排一次產生。呼叫端要在 tx() 裡，commit 後才會投遞。"""
    if force:
        await conn.execute(
            """INSERT INTO job_insights (job_post_id) VALUES ($1)
               ON CONFLICT (job_post_id) DO UPDATE SET status = 'pending', error = NULL""", job_post_id)
    else:
        n = await conn.execute("INSERT INTO job_insights (job_post_id) VALUES ($1) ON CONFLICT DO NOTHING", job_post_id)
        if n == "INSERT 0 0":
            return
    await enqueue(conn, "job_insight", {"job_post_id": str(job_post_id), "user_id": str(user_id)},
                  dedupe_key=f"job_insight:{job_post_id}")


async def handle(payload: dict) -> None:
    jid = UUID(payload["job_post_id"])
    async with db.connection() as conn:
        row = await conn.fetchrow(
            "UPDATE job_insights SET status = 'running' WHERE job_post_id = $1 AND status IN ('pending','running') RETURNING 1", jid)
        job = await conn.fetchrow("SELECT * FROM job_posts WHERE id = $1", jid)
    if not row or not job:
        return
    try:
        out = await ai_insight.analyze(dict(job), user_id=UUID(payload["user_id"]) if payload.get("user_id") else None)
        sql, args = ("""UPDATE job_insights SET status = 'ready', insight = $2, prompt_version = $3, error = NULL
                         WHERE job_post_id = $1 AND status = 'running'""", (out.model_dump(), ai_insight.PV_INSIGHT))
    except Exception as e:
        # AI 呼叫已重試過；畫面顯示「再試一次」
        sql, args = "UPDATE job_insights SET status = 'failed', error = $2 WHERE job_post_id = $1 AND status = 'running'", (str(e)[:300],)
    async with tx() as conn:
        if await conn.execute(sql, jid, *args) == "UPDATE 0" and await conn.fetchval(
                "SELECT status = 'pending' FROM job_insights WHERE job_post_id = $1", jid):
            # 產生期間職缺內容又改了（status 被改回 pending）：不覆蓋，用新內容再跑一次。
            # 這個工作還在 running，同一個 dedupe_key 排不進去，所以不帶 dedupe_key
            await enqueue(conn, "job_insight", payload)


def out(status: str | None, data: dict | None) -> dict:
    return {"status": status or "pending", **({"data": data} if status == "ready" and data else {})}
