"""面試建議：每個目標職缺一份。分析與再多產生都重讀職缺＋履歷，只附加、不刪除。"""
import datetime as dt
from uuid import UUID

from app.ai import text
from app.ai.agents import prep as pa
from app.core import db, redis
from app.core.errors import AppError, not_found
from app.modules.questions import service as questions
from app.modules.resumes import service as resumes
from app.workers.queue import enqueue, tx

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


async def _prep_by_target(conn: db.Connection, user_id: UUID, target_id: UUID) -> db.Record:
    p = await conn.fetchrow(
        "SELECT p.* FROM interview_preps p JOIN target_jobs t ON t.id = p.target_job_id WHERE t.id = $1 AND t.user_id = $2",
        target_id, user_id)
    if not p:
        raise not_found("找不到這份面試建議")
    return p


async def _prep(conn: db.Connection, user_id: UUID, prep_id: UUID) -> db.Record:
    p = await conn.fetchrow("SELECT * FROM interview_preps WHERE id = $1 AND user_id = $2", prep_id, user_id)
    if not p:
        raise not_found("找不到這份面試建議")
    return p


async def get(user_id: UUID, target_id: UUID) -> dict:
    async with db.connection() as conn:
        p = await _prep_by_target(conn, user_id, target_id)
        return await _out(conn, p)


async def _out(conn: db.Connection, p) -> dict:
    t = await conn.fetchrow("SELECT * FROM target_jobs WHERE id = $1", p["target_job_id"])
    j = await conn.fetchrow("SELECT id, company_name, title, updated_at FROM job_posts WHERE id = $1", p["job_post_id"])
    rid = p["resume_id"] or t["resume_id"]
    r = await conn.fetchrow("SELECT id, filename, updated_at, parse_status FROM resumes WHERE id = $1 AND deleted_at IS NULL", rid) if rid else None
    likely = await conn.fetch(
        """SELECT l.*, (i.id IS NOT NULL AND i.deleted_at IS NULL) AS in_set FROM prep_likely_questions l
             LEFT JOIN question_set_items i ON i.id = l.question_set_item_id
            WHERE l.prep_id = $1 ORDER BY l.order_no""", p["id"])
    check = await conn.fetch("SELECT * FROM prep_checklist_items WHERE prep_id = $1 ORDER BY sort_order", p["id"])
    g = await conn.fetchrow(
        "SELECT id, kind, status FROM prep_generations WHERE prep_id = $1 AND status IN ('pending','running') ORDER BY created_at DESC LIMIT 1",
        p["id"])
    last_gen = await conn.fetchval(
        "SELECT id FROM prep_generations WHERE prep_id = $1 AND status = 'succeeded' ORDER BY finished_at DESC LIMIT 1", p["id"])
    analyzed = p["analyzed_at"]
    stale = bool(analyzed and ((j["updated_at"] and p["job_updated_at"] and j["updated_at"] > p["job_updated_at"] + dt.timedelta(seconds=1))
                               or (t["resume_id"] != p["resume_id"])
                               or (r and r["updated_at"] > analyzed)))
    return {
        "id": str(p["id"]), "status": p["status"], "target_id": str(p["target_job_id"]),
        "basis": {"job": {"id": str(j["id"]), "company_name": j["company_name"], "title": j["title"], "updated_at": j["updated_at"]},
                  "resume": {"id": str(r["id"]), "filename": r["filename"], "parsed_at": r["updated_at"]} if r else None,
                  "analyzed_at": analyzed, "stale": stale},
        "match_score": p["match_score"], "verdict_title": p["verdict_title"], "verdict_text": p["verdict_text"],
        "strengths": p["strengths"] or [], "gaps": p["gaps"] or [], "directions": p["directions"] or [],
        "likely_questions": [{"id": str(l["id"]), "order_no": l["order_no"], "priority": l["priority"], "question": l["question"],
                              "why": l["why"], "tip": l["tip"], "source": l["source"], "created_at": l["created_at"],
                              "in_set": bool(l["in_set"]), "is_new": l["generation_id"] == last_gen and l["source"] != "initial"}
                             for l in likely],
        "checklist": [{"id": str(c["id"]), "order_no": c["sort_order"], "text": c["text"], "is_checked": c["is_checked"],
                       "source": c["source"], "created_at": c["created_at"],
                       "is_new": c["generation_id"] == last_gen and c["source"] != "initial"} for c in check],
        "pending_generation": {"id": str(g["id"]), "kind": g["kind"], "status": g["status"]} if g else None,
        "error": p["error"],
    }


async def start_generation(conn: db.Connection, prep_id: UUID, kind: str, params: dict, *, quiet: bool = False) -> UUID | None:
    running = await conn.fetchval("SELECT id FROM prep_generations WHERE prep_id = $1 AND status IN ('pending','running')", prep_id)
    if running:
        if quiet:
            return None
        raise AppError(409, "RESOURCE_NOT_READY", "Ava 正在整理建議，完成後再試一次", {"generation_id": str(running)})
    gid = await conn.fetchval("INSERT INTO prep_generations (prep_id, kind, params) VALUES ($1, $2, $3) RETURNING id",
                              prep_id, kind, params)
    if kind == "analyze":
        await conn.execute("UPDATE interview_preps SET status = CASE WHEN analyzed_at IS NULL THEN 'running' ELSE status END WHERE id = $1", prep_id)
    await enqueue(conn, "generate_prep" if kind == "analyze" else "extend_prep", {"generation_id": str(gid)})
    return gid


async def analyze(user_id: UUID, target_id: UUID, resume_id: UUID | None) -> dict:
    await redis.rate_limit("prep_gen", str(user_id))
    async with tx() as conn:
        p = await _prep_by_target(conn, user_id, target_id)
        if resume_id:
            await resumes.owned(conn, user_id, resume_id)
            await conn.execute("UPDATE target_jobs SET resume_id = $2 WHERE id = $1", target_id, resume_id)
        gid = await start_generation(conn, p["id"], "analyze", {"resume_id": str(resume_id) if resume_id else None})
    return {"generation_id": str(gid), "status": "pending"}


async def more(user_id: UUID, prep_id: UUID, kind: str, note: str, category: str | None, count: int) -> dict:
    await redis.rate_limit("prep_gen", str(user_id))
    async with tx() as conn:
        p = await _prep(conn, user_id, prep_id)
        if p["status"] != "ready":
            raise AppError(409, "RESOURCE_NOT_READY", "Ava 還在分析，完成後再產生")
        gid = await start_generation(conn, prep_id, kind, {"note": note.strip()[:300], "category": category, "count": count})
    return {"generation_id": str(gid), "status": "pending"}


async def generation(user_id: UUID, gid: UUID) -> dict:
    async with db.connection() as conn:
        g = await conn.fetchrow(
            "SELECT g.* FROM prep_generations g JOIN interview_preps p ON p.id = g.prep_id WHERE g.id = $1 AND p.user_id = $2",
            gid, user_id)
    if not g:
        raise not_found()
    cnt = int((g["params"] or {}).get("count") or 0)
    return {"id": str(g["id"]), "kind": g["kind"], "status": g["status"], "created_count": g["created_count"],
            "skipped_duplicates": max(0, cnt - g["created_count"]) if g["status"] == "succeeded" and cnt else 0,
            "error": g["error"]}


async def toggle(user_id: UUID, prep_id: UUID, item_id: UUID, checked: bool) -> None:
    async with db.connection() as conn:
        ok = await conn.fetchval(
            """UPDATE prep_checklist_items c SET is_checked = $3 FROM interview_preps p
                WHERE c.id = $2 AND c.prep_id = p.id AND p.id = $1 AND p.user_id = $4 RETURNING c.id""",
            prep_id, item_id, checked, user_id)
    if not ok:
        raise not_found()


async def add_to_set(user_id: UUID, prep_id: UUID, ids: list[UUID] | None) -> dict:
    async with tx() as conn:
        p = await _prep(conn, user_id, prep_id)
        set_id = await conn.fetchval("SELECT id FROM question_sets WHERE target_job_id = $1", p["target_job_id"])
        rows = await conn.fetch(
            """SELECT l.* FROM prep_likely_questions l LEFT JOIN question_set_items i ON i.id = l.question_set_item_id
                WHERE l.prep_id = $1 AND (i.id IS NULL OR i.deleted_at IS NOT NULL)
                  AND ($2::uuid[] IS NULL OR l.id = ANY($2)) ORDER BY l.order_no""", prep_id, ids)
        existing = [r["text"] for r in await conn.fetch(
            "SELECT text FROM question_set_items WHERE set_id = $1 AND deleted_at IS NULL", set_id)]
        added = skipped = 0
        for l in rows:
            if text.is_duplicate(l["question"], existing):
                same = await conn.fetchval(
                    "SELECT id FROM question_set_items WHERE set_id = $1 AND deleted_at IS NULL AND similarity(text, $2) > 0.3 ORDER BY similarity(text, $2) DESC LIMIT 1",
                    set_id, l["question"])
                if same:
                    await conn.execute("UPDATE prep_likely_questions SET question_set_item_id = $2 WHERE id = $1", l["id"], same)
                skipped += 1
                continue
            (iid,) = await questions.append_items(conn, set_id, [{"category": "情境題", "difficulty": "medium",
                                                                  "text": l["question"], "source_reason": l["why"]}], source="prep")
            await conn.execute("UPDATE prep_likely_questions SET question_set_item_id = $2 WHERE id = $1", l["id"], iid)
            await enqueue(conn, "build_rubric", {"item_id": str(iid)})
            existing.append(l["question"])
            added += 1
    return {"added": added, "skipped_duplicates": skipped}


# ---------- worker ----------
async def _context(conn: db.Connection, prep) -> tuple[dict, dict | None, db.Record, db.Record | None]:
    t = await conn.fetchrow("SELECT * FROM target_jobs WHERE id = $1", prep["target_job_id"])
    j = await conn.fetchrow("SELECT * FROM job_posts WHERE id = $1", prep["job_post_id"])
    r = await conn.fetchrow("SELECT * FROM resumes WHERE id = $1 AND deleted_at IS NULL", t["resume_id"]) if t["resume_id"] else None
    job = {k: j[k] for k in ("company_name", "title", "about", "summary", "duties", "requirements", "salary_text")}
    job["tags"] = list(j["tags"] or [])
    return job, resumes.snapshot(r), j, r


async def _finish(conn, gid, status: str, created: int = 0, error: str | None = None) -> None:
    await conn.execute("UPDATE prep_generations SET status = $2, created_count = $3, error = $4, finished_at = now() WHERE id = $1",
                       gid, status, created, error)


async def handle_analyze(payload: dict) -> None:
    gid = UUID(payload["generation_id"])
    async with db.connection() as conn:
        g = await conn.fetchrow("UPDATE prep_generations SET status = 'running' WHERE id = $1 AND status IN ('pending','running') RETURNING *", gid)
        if not g:
            return
        prep = await conn.fetchrow("SELECT * FROM interview_preps WHERE id = $1", g["prep_id"])
        job, resume, j, r = await _context(conn, prep)
        ex_q = [x["question"] for x in await conn.fetch("SELECT question FROM prep_likely_questions WHERE prep_id = $1", prep["id"])]
        ex_c = [x["text"] for x in await conn.fetch("SELECT text FROM prep_checklist_items WHERE prep_id = $1", prep["id"])]
    try:
        out = await pa.analyze(job=job, resume=resume, existing_questions=ex_q, existing_checklist=ex_c, user_id=prep["user_id"])
    except Exception as e:
        async with db.connection() as conn:
            await _finish(conn, gid, "failed", error=str(e)[:300])
            await conn.execute("UPDATE interview_preps SET status = CASE WHEN analyzed_at IS NULL THEN 'failed' ELSE status END, error = $2 WHERE id = $1",
                               prep["id"], str(e)[:300])
        return      # AI 呼叫已重試過；畫面顯示「再試一次」
    source = "initial" if prep["analyzed_at"] is None else "reanalyze"
    async with db.transaction() as conn:
        await conn.execute(
            """UPDATE interview_preps SET status = 'ready', match_score = $2, verdict_title = $3, verdict_text = $4,
                      strengths = $5, gaps = $6, directions = $7, analyzed_at = now(), job_updated_at = $8,
                      resume_id = $9, prompt_version = $10, error = NULL WHERE id = $1""",
            prep["id"], out.match_score if r else None, out.verdict_title if r else "先看看這個職缺重視什麼",
            out.verdict_text, out.strengths if r else [], out.gaps,
            [d.model_dump() for d in out.directions], j["updated_at"], r["id"] if r else None, pa.PV_ANALYZE)
        n = await _append_likely(conn, prep["id"], gid, [l.model_dump() for l in out.likely_questions], ex_q, source)
        n += await _append_check(conn, prep["id"], gid, out.checklist, ex_c, source)
        if r:
            await conn.execute(
                """INSERT INTO job_matches (resume_id, job_post_id, user_id, score, method) VALUES ($1, $2, $3, $4, 'llm')
                   ON CONFLICT (resume_id, job_post_id) DO UPDATE SET score = EXCLUDED.score, method = 'llm', computed_at = now()""",
                r["id"], j["id"], prep["user_id"], out.match_score)
        await _finish(conn, gid, "succeeded", n)


async def _append_likely(conn, prep_id, gid, items: list[dict], existing: list[str], source: str) -> int:
    n0 = await conn.fetchval("SELECT COALESCE(max(order_no), 0) FROM prep_likely_questions WHERE prep_id = $1", prep_id)
    added = 0
    for it in sorted(items, key=lambda x: PRIORITY_RANK.get(x["priority"], 1)):
        if text.is_duplicate(it["question"], existing):
            continue
        existing.append(it["question"])
        added += 1
        await conn.execute(
            """INSERT INTO prep_likely_questions (prep_id, generation_id, order_no, question, why, tip, priority, source)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            prep_id, gid, n0 + added, it["question"][:300], it["why"][:300], it["tip"][:400],
            it["priority"] if it["priority"] in PRIORITY_RANK else "medium", source)
    return added


async def _append_check(conn, prep_id, gid, items: list[str], existing: list[str], source: str) -> int:
    n0 = await conn.fetchval("SELECT COALESCE(max(sort_order), 0) FROM prep_checklist_items WHERE prep_id = $1", prep_id)
    added = 0
    for t in items:
        if not t.strip() or text.is_duplicate(t, existing):
            continue
        existing.append(t)
        added += 1
        await conn.execute("INSERT INTO prep_checklist_items (prep_id, generation_id, text, sort_order, source) VALUES ($1,$2,$3,$4,$5)",
                           prep_id, gid, t.strip()[:200], n0 + added, source)
    return added


async def handle_extend(payload: dict) -> None:
    gid = UUID(payload["generation_id"])
    async with db.connection() as conn:
        g = await conn.fetchrow("UPDATE prep_generations SET status = 'running' WHERE id = $1 AND status IN ('pending','running') RETURNING *", gid)
        if not g:
            return
        prep = await conn.fetchrow("SELECT * FROM interview_preps WHERE id = $1", g["prep_id"])
        job, resume, _, _ = await _context(conn, prep)
        if g["kind"] == "likely_more":
            existing = [x["question"] for x in await conn.fetch("SELECT question FROM prep_likely_questions WHERE prep_id = $1", prep["id"])]
            existing += [x["text"] for x in await conn.fetch(
                """SELECT i.text FROM question_set_items i JOIN question_sets qs ON qs.id = i.set_id
                    WHERE qs.target_job_id = $1 AND i.deleted_at IS NULL""", prep["target_job_id"])]
        else:
            existing = [x["text"] for x in await conn.fetch("SELECT text FROM prep_checklist_items WHERE prep_id = $1", prep["id"])]
    p = g["params"]
    try:
        if g["kind"] == "likely_more":
            items = await pa.extend_likely(job=job, resume=resume, existing=existing, note=p.get("note", ""),
                                           category=p.get("category"), count=int(p.get("count", 3)), user_id=prep["user_id"])
        else:
            items = await pa.extend_checklist(job=job, resume=resume, existing=existing, note=p.get("note", ""),
                                              count=int(p.get("count", 3)), user_id=prep["user_id"])
    except Exception as e:
        async with db.connection() as conn:
            await _finish(conn, gid, "failed", error=str(e)[:300])
        return
    async with db.transaction() as conn:
        if g["kind"] == "likely_more":
            n = await _append_likely(conn, prep["id"], gid, items, list(existing), "more")
        else:
            n = await _append_check(conn, prep["id"], gid, items, list(existing), "more")
        await _finish(conn, gid, "succeeded", n)
