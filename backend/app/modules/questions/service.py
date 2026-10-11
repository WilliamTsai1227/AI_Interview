"""面試題庫：每個目標職缺一組。生成（附加＋去重）、新增、修改、刪除、排序、追問規則。"""
from uuid import UUID

import structlog

from app.ai import text
from app.ai.agents import questions as qa
from app.core import db, redis
from app.core.errors import AppError, bad_request, not_found
from app.modules.resumes import service as resumes
from app.workers.queue import enqueue, tx

log = structlog.get_logger()


async def owned_set(conn: db.Connection, user_id: UUID, target_id: UUID) -> db.Record:
    s = await conn.fetchrow(
        """SELECT qs.*, t.job_post_id, t.resume_id FROM question_sets qs JOIN target_jobs t ON t.id = qs.target_job_id
            WHERE t.id = $1 AND t.user_id = $2""", target_id, user_id)
    if not s:
        raise not_found("找不到這個面試題庫")
    return s


def item_out(r, followups: list) -> dict:
    mode = r["followup_mode"]
    planned = [{"id": str(f["id"]), "order_no": f["order_no"], "text": f["text"]} for f in followups]
    count = len(planned) if mode == "planned" else (0 if mode == "none" else r["followup_count"])
    return {"id": str(r["id"]), "order_no": r["order_no"], "category": r["category"], "difficulty": r["difficulty"],
            "text": r["text"], "competency": r["competency"], "source": r["source"],
            "source_reason": r["source_reason"],
            "followup": {"mode": mode, "count": count, "planned": planned}}


async def _items(conn: db.Connection, set_id: UUID) -> list[dict]:
    items = await conn.fetch(
        "SELECT * FROM question_set_items WHERE set_id = $1 AND deleted_at IS NULL ORDER BY order_no", set_id)
    fus = await conn.fetch(
        """SELECT f.* FROM question_set_followups f JOIN question_set_items i ON i.id = f.item_id
            WHERE i.set_id = $1 AND i.deleted_at IS NULL ORDER BY f.order_no""", set_id)
    by: dict = {}
    for f in fus:
        by.setdefault(f["item_id"], []).append(f)
    return [item_out(i, by.get(i["id"], [])) for i in items]


async def list_(user_id: UUID, target_id: UUID) -> dict:
    async with db.connection() as conn:
        s = await owned_set(conn, user_id, target_id)
        g = await conn.fetchrow(
            "SELECT id, status FROM question_generations WHERE set_id = $1 AND status IN ('pending','running') ORDER BY created_at DESC LIMIT 1",
            s["id"])
        return {"set_id": str(s["id"]), "status": s["status"], "items": await _items(conn, s["id"]),
                "pending_generation": {"id": str(g["id"]), "status": g["status"]} if g else None}


async def start_generation(conn: db.Connection, user_id: UUID, set_id: UUID, params: dict) -> UUID:
    running = await conn.fetchval(
        "SELECT id FROM question_generations WHERE set_id = $1 AND status IN ('pending','running')", set_id)
    if running:
        raise AppError(409, "RESOURCE_NOT_READY", "Ava 正在出題，完成後再試一次", {"generation_id": str(running)})
    gid = await conn.fetchval(
        "INSERT INTO question_generations (user_id, set_id, params) VALUES ($1, $2, $3) RETURNING id",
        user_id, set_id, params)
    await enqueue(conn, "generate_questions", {"generation_id": str(gid)}, dedupe_key=f"qgen:{set_id}")
    return gid


async def generate(user_id: UUID, target_id: UUID, note: str, categories: list[str], difficulty: str, count: int) -> dict:
    await redis.rate_limit("question_gen", str(user_id))
    cats = [c for c in categories if c in text.CATEGORIES]
    async with tx() as conn:
        s = await owned_set(conn, user_id, target_id)
        gid = await start_generation(conn, user_id, s["id"], {"categories": cats, "difficulty": difficulty,
                                                              "count": count, "note": note.strip()[:300]})
    return {"id": str(gid), "status": "pending"}


async def generation(user_id: UUID, gid: UUID) -> dict:
    async with db.connection() as conn:
        g = await conn.fetchrow("SELECT * FROM question_generations WHERE id = $1 AND user_id = $2", gid, user_id)
    if not g:
        raise not_found()
    p = g["params"] or {}
    return {"id": str(g["id"]), "status": g["status"], "created_item_count": g["created_item_count"],
            "skipped_duplicates": max(0, int(p.get("count", 0)) - g["created_item_count"]) if g["status"] == "succeeded" else 0,
            "error": g["error"]}


async def _job_and_resume(conn: db.Connection, set_row) -> tuple[dict, dict | None]:
    j = await conn.fetchrow("SELECT * FROM job_posts WHERE id = $1", set_row["job_post_id"])
    r = await conn.fetchrow("SELECT * FROM resumes WHERE id = $1", set_row["resume_id"]) if set_row["resume_id"] else None
    job = {k: j[k] for k in ("company_name", "title", "about", "summary", "duties", "requirements")}
    job["tags"] = list(j["tags"] or [])
    return job, resumes.snapshot(r)


async def append_items(conn: db.Connection, set_id: UUID, items: list[dict], *, source: str,
                       generation_id: UUID | None = None, at_front: bool = False) -> list[UUID]:
    """附加題目（去重已在呼叫端處理）。at_front：報告的弱項加入題組時放在最前面。"""
    if not items:
        return []
    n = await conn.fetchval("SELECT count(*) FROM question_set_items WHERE set_id = $1 AND deleted_at IS NULL", set_id)
    if at_front:
        await conn.execute(
            "UPDATE question_set_items SET order_no = order_no + $2 WHERE set_id = $1 AND deleted_at IS NULL", set_id, len(items))
    ids = []
    for i, it in enumerate(items):
        order = i + 1 if at_front else n + i + 1
        mode, cnt = it.get("followup_mode") or qa.default_followup(it["category"])[0], it.get("followup_count") or 1
        ids.append(await conn.fetchval(
            """
            INSERT INTO question_set_items (set_id, generation_id, order_no, category, difficulty, text, competency,
                                            expected_points, rubric, source, source_reason, followup_mode, followup_count)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) RETURNING id
            """, set_id, generation_id, order, it["category"], it.get("difficulty", "medium"), it["text"][:500],
            it.get("competency"), it.get("expected_points", []), it.get("rubric"), source, it.get("source_reason"),
            mode, cnt))
    await conn.execute("UPDATE question_sets SET updated_at = now() WHERE id = $1", set_id)
    return ids


async def handle_generate(payload: dict) -> None:
    gid = UUID(payload["generation_id"])
    async with db.connection() as conn:
        g = await conn.fetchrow("UPDATE question_generations SET status = 'running' WHERE id = $1 AND status IN ('pending','running') RETURNING *", gid)
        if not g:
            return
        s = await conn.fetchrow(
            "SELECT qs.*, t.job_post_id, t.resume_id FROM question_sets qs JOIN target_jobs t ON t.id = qs.target_job_id WHERE qs.id = $1",
            g["set_id"])
        job, resume = await _job_and_resume(conn, s)
        existing = [r["text"] for r in await conn.fetch(
            "SELECT text FROM question_set_items WHERE set_id = $1 AND deleted_at IS NULL", s["id"])]
        lang = (await conn.fetchval("SELECT preferences->>'default_language' FROM users WHERE id = $1", g["user_id"])) or "zh"
    p = g["params"]
    async with redis.lock(f"qgen:{s['id']}", 120):
        try:
            items = await qa.plan_questions(job=job, resume=resume, categories=p.get("categories", []),
                                            difficulty=p.get("difficulty", "medium"), count=int(p.get("count", 5)),
                                            note=p.get("note", ""), existing=existing,
                                            language="en" if lang == "en" else "zh", user_id=g["user_id"])
        except Exception as e:
            async with db.connection() as conn:
                await conn.execute("UPDATE question_generations SET status = 'failed', error = $2, finished_at = now() WHERE id = $1",
                                   gid, str(e)[:300])
                await conn.execute("UPDATE question_sets SET status = 'ready' WHERE id = $1", s["id"])
            log.warning("question_generation_failed", generation_id=str(gid), error=str(e))
            return      # AI 呼叫已重試過；標記失敗讓使用者在畫面上再試，不讓背景工作空轉
        async with tx() as conn:
            await append_items(conn, s["id"], items, source="ai", generation_id=gid)
            await conn.execute(
                "UPDATE question_generations SET status = 'succeeded', created_item_count = $2, finished_at = now() WHERE id = $1",
                gid, len(items))
            await conn.execute("UPDATE question_sets SET status = 'ready' WHERE id = $1", s["id"])


async def _owned_item(conn: db.Connection, user_id: UUID, target_id: UUID, item_id: UUID) -> db.Record:
    s = await owned_set(conn, user_id, target_id)
    it = await conn.fetchrow("SELECT * FROM question_set_items WHERE id = $1 AND set_id = $2 AND deleted_at IS NULL FOR UPDATE",
                             item_id, s["id"])
    if not it:
        raise not_found("找不到這一題")
    return it


async def _one(conn: db.Connection, item_id: UUID) -> dict:
    it = await conn.fetchrow("SELECT * FROM question_set_items WHERE id = $1", item_id)
    fus = await conn.fetch("SELECT * FROM question_set_followups WHERE item_id = $1 ORDER BY order_no", item_id)
    return item_out(it, fus)


def _check_followup(mode: str, count: int | None, planned: list[str] | None) -> tuple[int, list[str]]:
    if mode == "fixed" and not (count and 1 <= count <= 3):
        raise bad_request("固定追問次數要在 1–3 次", {"count": "1–3"})
    if mode == "adaptive" and not (count and 1 <= count <= 2):
        raise bad_request("Ava 判斷的追問上限要在 1–2 次", {"count": "1–2"})
    planned = [p.strip() for p in (planned or []) if p and p.strip()]
    if mode == "planned" and len(planned) > 5:
        raise bad_request("預設追問最多 5 題", {"planned": "≤ 5"})
    if any(len(p) > 200 for p in planned):
        raise bad_request("每個追問最多 200 字", {"planned": "≤ 200"})
    return count or 1, planned


async def add(user_id: UUID, target_id: UUID, text_: str, category: str, difficulty: str, followup: dict | None) -> dict:
    t = text_.strip()
    if not t:
        raise bad_request("題目內容不能是空的", {"text": "required"})
    async with tx() as conn:
        s = await owned_set(conn, user_id, target_id)
        fu = followup or {"mode": "adaptive", "count": 1}
        cnt, planned = _check_followup(fu["mode"], fu.get("count"), fu.get("planned"))
        (iid,) = await append_items(conn, s["id"], [{"category": category or "自訂", "difficulty": difficulty, "text": t,
                                                    "followup_mode": fu["mode"], "followup_count": cnt}], source="user")
        for i, p in enumerate(planned):
            await conn.execute("INSERT INTO question_set_followups (item_id, order_no, text) VALUES ($1, $2, $3)", iid, i + 1, p)
        await enqueue(conn, "build_rubric", {"item_id": str(iid)})
        return await _one(conn, iid)


async def patch(user_id: UUID, target_id: UUID, item_id: UUID, text_: str | None, category: str | None,
                difficulty: str | None) -> dict:
    async with tx() as conn:
        it = await _owned_item(conn, user_id, target_id, item_id)
        if text_ is not None:
            if not text_.strip():
                raise bad_request("題目內容不能是空的", {"text": "required"})
            if text_.strip() != it["text"]:
                await conn.execute("UPDATE question_set_items SET text = $2, rubric = NULL WHERE id = $1", item_id, text_.strip()[:500])
                await enqueue(conn, "build_rubric", {"item_id": str(item_id)})
        if category:
            await conn.execute("UPDATE question_set_items SET category = $2 WHERE id = $1", item_id, category[:20])
        if difficulty in text.DIFFICULTIES:
            await conn.execute("UPDATE question_set_items SET difficulty = $2 WHERE id = $1", item_id, difficulty)
        return await _one(conn, item_id)


async def set_followups(user_id: UUID, target_id: UUID, item_id: UUID, mode: str, count: int | None,
                        planned: list[str] | None) -> dict:
    async with tx() as conn:
        it = await _owned_item(conn, user_id, target_id, item_id)
        if mode == "planned" and planned is None:   # 只切換模式時保留既有預設追問
            planned = [f["text"] for f in await conn.fetch(
                "SELECT text FROM question_set_followups WHERE item_id = $1 ORDER BY order_no", item_id)]
        cnt, planned = _check_followup(mode, count, planned)
        await conn.execute("UPDATE question_set_items SET followup_mode = $2, followup_count = $3 WHERE id = $1",
                           item_id, mode, cnt if mode in ("fixed", "adaptive") else it["followup_count"] if mode == "none" else 1)
        if mode == "planned":
            await conn.execute("DELETE FROM question_set_followups WHERE item_id = $1", item_id)
            for i, p in enumerate(planned):
                await conn.execute("INSERT INTO question_set_followups (item_id, order_no, text) VALUES ($1, $2, $3)",
                                   item_id, i + 1, p)
        return await _one(conn, item_id)


async def delete(user_id: UUID, target_id: UUID, item_id: UUID) -> None:
    async with db.transaction() as conn:
        it = await _owned_item(conn, user_id, target_id, item_id)
        await conn.execute(
            """UPDATE question_set_items SET deleted_at = now(),
                      order_no = (SELECT LEAST(COALESCE(min(order_no), 0), 0) - 1 FROM question_set_items WHERE set_id = $2)
                WHERE id = $1""", item_id, it["set_id"])
        await conn.execute("DELETE FROM question_set_followups WHERE item_id = $1", item_id)
        await conn.execute(
            """
            UPDATE question_set_items i SET order_no = x.rn FROM (
              SELECT id, row_number() OVER (ORDER BY order_no) AS rn FROM question_set_items
               WHERE set_id = $1 AND deleted_at IS NULL) x
             WHERE i.id = x.id
            """, it["set_id"])
        await conn.execute("UPDATE prep_likely_questions SET question_set_item_id = NULL WHERE question_set_item_id = $1", item_id)


async def reorder(user_id: UUID, target_id: UUID, item_ids: list[UUID]) -> dict:
    async with db.transaction() as conn:
        s = await owned_set(conn, user_id, target_id)
        cur = {r["id"] for r in await conn.fetch(
            "SELECT id FROM question_set_items WHERE set_id = $1 AND deleted_at IS NULL", s["id"])}
        if set(item_ids) != cur or len(item_ids) != len(cur):
            raise bad_request("排序要包含題組中的全部題目")
        for i, iid in enumerate(item_ids):
            await conn.execute("UPDATE question_set_items SET order_no = $2 WHERE id = $1", iid, i + 1)
        return {"items": await _items(conn, s["id"])}


async def handle_build_rubric(payload: dict) -> None:
    async with db.connection() as conn:
        it = await conn.fetchrow(
            """SELECT i.*, j.company_name, j.title, j.requirements FROM question_set_items i
                 JOIN question_sets qs ON qs.id = i.set_id JOIN target_jobs t ON t.id = qs.target_job_id
                 JOIN job_posts j ON j.id = t.job_post_id WHERE i.id = $1 AND i.deleted_at IS NULL""",
            UUID(payload["item_id"]))
    if not it or it["rubric"]:
        return
    r = await qa.build_rubric(it["text"], {"company_name": it["company_name"], "title": it["title"],
                                           "requirements": it["requirements"]})
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE question_set_items SET rubric = $2, competency = COALESCE(competency, $3), expected_points = $4 WHERE id = $1",
            it["id"], r["rubric"], r["competency"], r["expected_points"])
