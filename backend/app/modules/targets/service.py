"""目標職缺：練習單位。加入時一次完成「建立職缺 → 解析 → 出第一組題目 → 產生面試建議」。"""
from uuid import UUID

from app.ai import text
from app.core import db, redis
from app.core.errors import AppError, not_found
from app.modules.jobs import service as jobs
from app.modules.resumes import service as resumes
from app.workers.queue import enqueue, tx

INITIAL_QUESTIONS = 8

_SELECT = """
SELECT t.*, j.source, j.company_name, j.title, j.location_text, j.city, j.tags, j.parse_status, j.salary_text,
       j.employment_type, j.work_mode, j.work_mode_note, j.about, j.summary, j.duties, j.requirements, j.source_site,
       j.source_url, j.last_verified_at, j.updated_at AS job_updated_at, j.raw_text,
       r.filename AS resume_filename,
       qs.id AS set_id, qs.status AS set_status,
       (SELECT count(*) FROM question_set_items i WHERE i.set_id = qs.id AND i.deleted_at IS NULL) AS item_count,
       p.id AS prep_id, p.status AS prep_status, p.match_score,
       (SELECT count(*) FROM prep_likely_questions l WHERE l.prep_id = p.id) AS likely_count,
       (SELECT count(*) FILTER (WHERE c.is_checked) FROM prep_checklist_items c WHERE c.prep_id = p.id) AS checklist_done,
       (SELECT count(*) FROM prep_checklist_items c WHERE c.prep_id = p.id) AS checklist_total,
       lr.session_id AS last_session_id, lr.overall_score AS last_score, lr.ended_at AS last_ended_at,
       (SELECT count(*) FROM question_generations g WHERE g.set_id = qs.id AND g.status IN ('pending','running')) AS gen_running
  FROM target_jobs t
  JOIN job_posts j ON j.id = t.job_post_id
  LEFT JOIN resumes r ON r.id = t.resume_id AND r.deleted_at IS NULL
  LEFT JOIN question_sets qs ON qs.target_job_id = t.id
  LEFT JOIN interview_preps p ON p.target_job_id = t.id
  LEFT JOIN LATERAL (
       SELECT s.id AS session_id, rep.overall_score, s.ended_at
         FROM interview_sessions s JOIN interview_reports rep ON rep.session_id = s.id AND rep.status = 'ready'
        WHERE s.target_job_id = t.id ORDER BY s.ended_at DESC NULLS LAST LIMIT 1) lr ON true
"""


def target_out(r, detail: bool = False) -> dict:
    r = dict(r)
    job = {"id": str(r["job_post_id"]), "source": r["source"], "company_name": r["company_name"], "title": r["title"],
           "location_text": r["location_text"], "city": r["city"], "tags": list(r["tags"] or []),
           "parse_status": r["parse_status"], "salary_text": r["salary_text"], "employment_type": r["employment_type"],
           "work_mode": r["work_mode"], "work_mode_note": r["work_mode_note"], "source_site": r["source_site"],
           "source_site_label": jobs.SITE_LABEL.get(r["source_site"] or "", r["source_site"]),
           "source_url": r["source_url"], "last_verified_at": r["last_verified_at"], "updated_at": r["job_updated_at"]}
    if detail:
        job.update({"about": r["about"], "summary": r["summary"], "duties": r["duties"] or [],
                    "requirements": r["requirements"] or [], "raw_text": r["raw_text"]})
    set_status = r["set_status"] or "preparing"
    if r["gen_running"] and set_status == "ready" and not r["item_count"]:
        set_status = "preparing"
    return {
        "id": str(r["id"]), "status": r["status"], "job": job,
        "resume": {"id": str(r["resume_id"]), "filename": r["resume_filename"]} if r["resume_id"] else None,
        "match_score": r["match_score"],
        "question_set": {"id": str(r["set_id"]) if r["set_id"] else None, "status": set_status,
                         "item_count": r["item_count"] or 0},
        "prep": {"id": str(r["prep_id"]) if r["prep_id"] else None, "status": r["prep_status"] or "pending",
                 "likely_count": r["likely_count"] or 0, "checklist_done": r["checklist_done"] or 0,
                 "checklist_total": r["checklist_total"] or 0},
        "last_report": {"session_id": str(r["last_session_id"]), "overall_score": r["last_score"],
                        "ended_at": r["last_ended_at"]} if r["last_session_id"] else None,
        "last_practiced_at": r["last_practiced_at"], "created_at": r["created_at"],
    }


async def fetch(conn: db.Connection, user_id: UUID, target_id: UUID, *, active_only: bool = False) -> db.Record:
    r = await conn.fetchrow(f"{_SELECT} WHERE t.id = $1 AND t.user_id = $2", target_id, user_id)
    if not r or (active_only and r["status"] != "active"):
        raise not_found("找不到這個目標職缺")
    return r


async def list_(user_id: UUID) -> list[dict]:
    async with db.connection() as conn:
        rs = await conn.fetch(
            f"{_SELECT} WHERE t.user_id = $1 AND t.status = 'active' "
            "ORDER BY COALESCE(t.last_practiced_at, t.created_at) DESC LIMIT 30", user_id)
    return [target_out(r) for r in rs]


async def get(user_id: UUID, target_id: UUID) -> dict:
    async with db.connection() as conn:
        return target_out(await fetch(conn, user_id, target_id), detail=True)


async def _create_children(conn: db.Connection, user_id: UUID, target_id: UUID, job_id: UUID,
                           resume_id: UUID | None, job_ready: bool) -> None:
    set_id = await conn.fetchval(
        "INSERT INTO question_sets (user_id, target_job_id) VALUES ($1, $2) RETURNING id", user_id, target_id)
    prep_id = await conn.fetchval(
        "INSERT INTO interview_preps (user_id, target_job_id, job_post_id, resume_id) VALUES ($1, $2, $3, $4) RETURNING id",
        user_id, target_id, job_id, resume_id)
    if job_ready:
        await _start_initial(conn, user_id, set_id, prep_id, resume_id)


async def _start_initial(conn: db.Connection, user_id: UUID, set_id: UUID, prep_id: UUID, resume_id: UUID | None) -> None:
    from app.modules.preps import service as preps
    from app.modules.questions import service as questions
    await questions.start_generation(conn, user_id, set_id, {"categories": [], "difficulty": "medium",
                                                             "count": INITIAL_QUESTIONS, "note": "", "initial": True})
    await preps.start_generation(conn, prep_id, "analyze",
                                 {"resume_id": str(resume_id) if resume_id else None, "initial": True}, quiet=True)


async def after_job_ready(conn: db.Connection, target_id: UUID) -> None:
    t = await conn.fetchrow(
        """
        SELECT t.user_id, t.resume_id, qs.id AS set_id, p.id AS prep_id FROM target_jobs t
          JOIN question_sets qs ON qs.target_job_id = t.id JOIN interview_preps p ON p.target_job_id = t.id
         WHERE t.id = $1
        """, target_id)
    if t:
        await _start_initial(conn, t["user_id"], t["set_id"], t["prep_id"], t["resume_id"])


async def create(user_id: UUID, raw_text: str | None, job_post_id: UUID | None, resume_id: UUID | None) -> tuple[int, dict]:
    await redis.rate_limit("target_create", str(user_id))
    async with tx() as conn:
        if resume_id:
            await resumes.owned(conn, user_id, resume_id)
        else:
            resume_id = await resumes.primary_id(conn, user_id)
        if job_post_id:
            job = await conn.fetchrow(
                """SELECT * FROM job_posts WHERE id = $1 AND deleted_at IS NULL
                     AND ((source = 'catalog' AND review_status = 'published') OR owner_user_id = $2)""",
                job_post_id, user_id)
            if not job:
                raise not_found("找不到這個職缺")
            existing = await conn.fetchrow("SELECT id, status FROM target_jobs WHERE user_id = $1 AND job_post_id = $2",
                                           user_id, job_post_id)
            if existing:
                if existing["status"] != "active":
                    await conn.execute("UPDATE target_jobs SET status = 'active' WHERE id = $1", existing["id"])
                return 200, target_out(await fetch(conn, user_id, existing["id"]), detail=True)
            tid = await conn.fetchval(
                "INSERT INTO target_jobs (user_id, job_post_id, resume_id) VALUES ($1, $2, $3) RETURNING id",
                user_id, job_post_id, resume_id)
            await _create_children(conn, user_id, tid, job_post_id, resume_id, job_ready=True)
        else:
            raw = (raw_text or "").strip()
            if len(raw) < 20 or len(raw.splitlines()[0]) < 2:
                raise AppError(422, "JD_UNREADABLE", "內容太短，至少要有職稱和工作內容")
            p = text.rough_parse_jd(raw)
            jid = await conn.fetchval(
                """
                INSERT INTO job_posts (source, owner_user_id, company_name, title, raw_text, parse_status, tags, search_text)
                VALUES ('user', $1, $2, $3, $4, 'pending', $5, $6) RETURNING id
                """, user_id, p["company_name"][:120], p["title"][:120], raw[:20000], p["tags"],
                jobs.search_text(p["company_name"], p["title"], p["tags"]))
            tid = await conn.fetchval(
                "INSERT INTO target_jobs (user_id, job_post_id, resume_id) VALUES ($1, $2, $3) RETURNING id",
                user_id, jid, resume_id)
            await _create_children(conn, user_id, tid, jid, resume_id, job_ready=False)
            await enqueue(conn, "parse_job", {"job_post_id": str(jid), "target_id": str(tid)},
                          dedupe_key=f"parse_job:{jid}")
        r = await fetch(conn, user_id, tid)
    return 202, target_out(r, detail=True)


async def patch(user_id: UUID, target_id: UUID, resume_id: UUID | None, clear_resume: bool, job: dict | None) -> dict:
    from app.modules.preps import service as preps
    async with tx() as conn:
        t = await fetch(conn, user_id, target_id, active_only=True)
        changed = False
        if resume_id or clear_resume:
            if resume_id:
                await resumes.owned(conn, user_id, resume_id)
            await conn.execute("UPDATE target_jobs SET resume_id = $2 WHERE id = $1", target_id, resume_id)
            changed = True
        if job:
            if t["source"] != "user":
                raise AppError(400, "VALIDATION_ERROR", "平台職缺的內容不能修改")
            fields = {k: v for k, v in job.items() if v is not None}
            if fields:
                sets, args = [], [t["job_post_id"]]
                for k, v in fields.items():
                    args.append(v)
                    sets.append(f"{k} = ${len(args)}")
                await conn.execute(f"UPDATE job_posts SET {', '.join(sets)} WHERE id = $1", *args)
                j = await conn.fetchrow("SELECT company_name, title, tags FROM job_posts WHERE id = $1", t["job_post_id"])
                await conn.execute("UPDATE job_posts SET search_text = $2 WHERE id = $1", t["job_post_id"],
                                   jobs.search_text(j["company_name"], j["title"], list(j["tags"] or [])))
                changed = True
        if changed and t["prep_id"]:
            running = await conn.fetchval(
                "SELECT 1 FROM prep_generations WHERE prep_id = $1 AND status IN ('pending','running')", t["prep_id"])
            if not running:
                await preps.start_generation(conn, t["prep_id"], "analyze",
                                             {"resume_id": str(resume_id) if resume_id else None}, quiet=True)
        r = await fetch(conn, user_id, target_id)
    return target_out(r, detail=True)


async def archive(user_id: UUID, target_id: UUID) -> None:
    async with db.transaction() as conn:
        await fetch(conn, user_id, target_id)
        active = await conn.fetchval(
            "SELECT id FROM interview_sessions WHERE target_job_id = $1 AND status IN ('preparing','ready','in_progress','paused')",
            target_id)
        if active:
            raise AppError(409, "ACTIVE_SESSION_EXISTS", "這個職缺還有未結束的面試", {"session_id": str(active)})
        await conn.execute("UPDATE target_jobs SET status = 'archived' WHERE id = $1", target_id)
