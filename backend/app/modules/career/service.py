"""職涯差距分析：個人檔案＋履歷 → 比對使用者自己的目標職缺 → 能力差距 → 準備計畫（大項目／小項目）。

比對對象是使用者加入的目標職缺（自己挑的、最可靠）。沒有目標職缺時，只依個人檔案與履歷分析，
並建議加入目標職缺。不再使用平台職缺或網路搜尋（品質不穩，見 spec/features/career-plan.md）。
"""
from uuid import UUID

from app.ai.agents import career as ca
from app.core import db, redis
from app.core.errors import AppError, not_found
from app.modules.resumes import service as resumes
from app.workers.queue import enqueue, tx

MAX_TARGETS = 10
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
PROFILE_FIELDS = ("self_intro", "experience_note", "portfolio_links", "resume_id", "salary_min", "salary_period",
                  "industries", "roles", "duties_pref", "work_hours", "locations", "work_modes")


def _profile_out(r) -> dict:
    if not r:
        return {"self_intro": "", "experience_note": "", "portfolio_links": [], "resume_id": None, "salary_min": None, "salary_period": "month", "industries": [],
                "roles": [], "duties_pref": "", "work_hours": "", "locations": [], "work_modes": [], "updated_at": None}
    d = {k: r[k] for k in PROFILE_FIELDS}
    d["resume_id"] = str(d["resume_id"]) if d["resume_id"] else None
    return {**d, "updated_at": r["updated_at"]}


async def get_profile(user_id: UUID) -> dict:
    async with db.connection() as conn:
        return _profile_out(await conn.fetchrow("SELECT * FROM career_profiles WHERE user_id = $1", user_id))


async def put_profile(user_id: UUID, data: dict) -> dict:
    async with db.connection() as conn:
        if data["resume_id"] and not await conn.fetchval(
                "SELECT 1 FROM resumes WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL", data["resume_id"], user_id):
            raise not_found("找不到這份履歷")
        r = await conn.fetchrow(
            """
            INSERT INTO career_profiles (user_id, self_intro, experience_note, portfolio_links, resume_id, salary_min, salary_period,
                                         industries, roles, duties_pref, work_hours, locations, work_modes)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            ON CONFLICT (user_id) DO UPDATE SET self_intro = EXCLUDED.self_intro, experience_note = EXCLUDED.experience_note,
                 portfolio_links = EXCLUDED.portfolio_links, resume_id = EXCLUDED.resume_id,
                 salary_min = EXCLUDED.salary_min, salary_period = EXCLUDED.salary_period, industries = EXCLUDED.industries,
                 roles = EXCLUDED.roles, duties_pref = EXCLUDED.duties_pref, work_hours = EXCLUDED.work_hours,
                 locations = EXCLUDED.locations, work_modes = EXCLUDED.work_modes, updated_at = now()
            RETURNING *
            """, user_id, *(data[k] for k in PROFILE_FIELDS))
    return _profile_out(r)


# ---------- 建立分析 ----------
async def start(user_id: UUID) -> dict:
    await redis.rate_limit("career_plan", str(user_id))
    async with tx() as conn:
        prof = await conn.fetchrow("SELECT * FROM career_profiles WHERE user_id = $1", user_id)
        p = _profile_out(prof)
        if not (p["self_intro"].strip() or p["roles"] or p["industries"] or p["duties_pref"].strip()):
            raise AppError(400, "VALIDATION_ERROR", "請先填寫自我介紹或求職意向（想做的職務、產業或工作內容）",
                           {"fields": {"profile": "empty"}})
        running = await conn.fetchval(
            "SELECT id FROM career_plans WHERE user_id = $1 AND status IN ('pending','running')", user_id)
        if running:
            return {"id": str(running), "status": "running"}
        rid = (await conn.fetchval("SELECT id FROM resumes WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
                                   UUID(p["resume_id"]), user_id) if p["resume_id"] else None) \
            or await conn.fetchval("SELECT id FROM resumes WHERE user_id = $1 AND is_primary AND deleted_at IS NULL", user_id)
        pid = await conn.fetchval(
            "INSERT INTO career_plans (user_id, resume_id, profile_snapshot) VALUES ($1,$2,$3) RETURNING id",
            user_id, rid, {k: v for k, v in p.items() if k != "updated_at"})
        await enqueue(conn, "build_career_plan", {"plan_id": str(pid)}, dedupe_key=f"career:{user_id}")
    return {"id": str(pid), "status": "pending"}


# ---------- 讀取 ----------
async def _plan_out(conn: db.Connection, p) -> dict:
    jobs_rs = await conn.fetch(
        """SELECT cj.*, j.company_name, j.title, j.city, j.salary_text, j.source_url, j.source_site, j.work_mode, t.id AS target_id
             FROM career_plan_jobs cj JOIN job_posts j ON j.id = cj.job_post_id
             LEFT JOIN target_jobs t ON t.job_post_id = j.id AND t.user_id = $2 AND t.status = 'active'
            WHERE cj.plan_id = $1 ORDER BY cj.match_score DESC NULLS LAST""", p["id"], p["user_id"])
    areas = await conn.fetch("SELECT * FROM career_plan_areas WHERE plan_id = $1 ORDER BY sort_order", p["id"])
    tasks = await conn.fetch("SELECT * FROM career_plan_tasks WHERE plan_id = $1 ORDER BY sort_order", p["id"])
    by_area: dict = {}
    for t in tasks:
        by_area.setdefault(t["area_id"], []).append({"id": str(t["id"]), "text": t["text"], "is_checked": t["is_checked"]})
    return {
        "id": str(p["id"]), "status": p["status"], "created_at": p["created_at"], "finished_at": p["finished_at"],
        "summary": p["summary"], "readiness_score": p["readiness_score"], "strengths": list(p["strengths"] or []),
        "gaps": p["gaps"] or [], "has_resume": p["resume_id"] is not None,
        "error": p["error"], "profile": p["profile_snapshot"],
        "jobs": [{"id": str(r["job_post_id"]), "found_by": r["found_by"], "company_name": r["company_name"], "title": r["title"],
                  "city": r["city"], "salary_text": r["salary_text"], "source_url": r["source_url"],
                  "source_site": r["source_site"], "work_mode": r["work_mode"], "match_score": r["match_score"],
                  "reason": r["reason"], "gaps": list(r["gaps"] or []),
                  "target_id": str(r["target_id"]) if r["target_id"] else None} for r in jobs_rs],
        "areas": [{"id": str(a["id"]), "title": a["title"], "why": a["why"], "priority": a["priority"],
                   "tasks": by_area.get(a["id"], [])} for a in areas],
        "progress": {"done": sum(1 for t in tasks if t["is_checked"]), "total": len(tasks)},
    }


async def get_plan(user_id: UUID, plan_id: UUID | None) -> dict | None:
    async with db.connection() as conn:
        p = (await conn.fetchrow("SELECT * FROM career_plans WHERE id = $1 AND user_id = $2", plan_id, user_id) if plan_id
             else await conn.fetchrow("SELECT * FROM career_plans WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1", user_id))
        if not p:
            if plan_id:
                raise not_found("找不到這份分析")
            return None
        return await _plan_out(conn, p)


async def list_plans(user_id: UUID) -> list[dict]:
    async with db.connection() as conn:
        rs = await conn.fetch(
            "SELECT id, status, readiness_score, created_at FROM career_plans WHERE user_id = $1 ORDER BY created_at DESC LIMIT 20", user_id)
    return [{"id": str(r["id"]), "status": r["status"], "readiness_score": r["readiness_score"], "created_at": r["created_at"]} for r in rs]


async def toggle_task(user_id: UUID, plan_id: UUID, task_id: UUID, checked: bool) -> None:
    async with db.connection() as conn:
        ok = await conn.fetchval(
            """UPDATE career_plan_tasks t SET is_checked = $3 FROM career_plans p
                WHERE t.id = $2 AND t.plan_id = p.id AND p.id = $1 AND p.user_id = $4 RETURNING t.id""",
            plan_id, task_id, checked, user_id)
    if not ok:
        raise not_found()


# ---------- worker ----------
async def _target_jobs(conn: db.Connection, user_id: UUID) -> list[db.Record]:
    """使用者目前的目標職缺（最近練習或加入的優先）。"""
    return await conn.fetch(
        """SELECT j.* FROM target_jobs t JOIN job_posts j ON j.id = t.job_post_id
            WHERE t.user_id = $1 AND t.status = 'active' AND j.deleted_at IS NULL
            ORDER BY COALESCE(t.last_practiced_at, t.created_at) DESC LIMIT $2""", user_id, MAX_TARGETS)


def _job_dict(r) -> dict:
    return {"company_name": r["company_name"], "title": r["title"], "salary_text": r["salary_text"], "city": r["city"],
            "duties": list(r["duties"] or []), "requirements": list(r["requirements"] or []), "tags": list(r["tags"] or [])}


async def handle_build(payload: dict) -> None:
    pid = UUID(payload["plan_id"])
    async with db.connection() as conn:
        p = await conn.fetchrow("UPDATE career_plans SET status = 'running' WHERE id = $1 AND status IN ('pending','running') RETURNING *", pid)
        if not p:
            return
        profile = p["profile_snapshot"]
        r = await conn.fetchrow("SELECT * FROM resumes WHERE id = $1 AND deleted_at IS NULL", p["resume_id"]) if p["resume_id"] else None
        resume = resumes.snapshot(r)
        targets = await _target_jobs(conn, p["user_id"])
    uid = p["user_id"]
    cands = [(t["id"], _job_dict(t)) for t in targets]
    try:
        out = await ca.build_plan(profile=profile, resume=resume, jobs=[c[1] for c in cands], user_id=uid)
    except Exception as e:
        async with db.connection() as conn:
            await conn.execute("UPDATE career_plans SET status = 'failed', error = $2, finished_at = now() WHERE id = $1", pid, str(e)[:300])
        return
    fits = {f.index: f for f in out.job_fits if 0 <= f.index < len(cands)}
    async with db.transaction() as conn:
        await conn.execute(
            """UPDATE career_plans SET status = 'ready', summary = $2, readiness_score = $3, strengths = $4, gaps = $5,
                      prompt_version = $6, error = NULL, finished_at = now()
                WHERE id = $1""",
            pid, out.summary[:300], max(0, min(100, out.readiness_score)), [s[:80] for s in out.strengths[:5]],
            [{"skill": g.skill[:40], "severity": g.severity if g.severity in PRIORITY_RANK else "medium", "detail": g.detail[:160]}
             for g in out.gaps[:8]], ca.PV_PLAN)
        for i, (jid, _) in enumerate(cands):
            f = fits.get(i)
            await conn.execute(
                "INSERT INTO career_plan_jobs (plan_id, job_post_id, found_by, match_score, reason, gaps) VALUES ($1,$2,'target',$3,$4,$5)",
                pid, jid, max(0, min(100, f.score)) if f else None, f.reason[:160] if f else None,
                [g[:60] for g in f.gaps[:3]] if f else [])
        for n, a in enumerate(sorted(out.areas[:6], key=lambda x: PRIORITY_RANK.get(x.priority, 1))):
            aid = await conn.fetchval(
                "INSERT INTO career_plan_areas (plan_id, title, why, priority, sort_order) VALUES ($1,$2,$3,$4,$5) RETURNING id",
                pid, a.title[:60], a.why[:160], a.priority if a.priority in PRIORITY_RANK else "medium", n)
            for m, t in enumerate(x for x in a.tasks[:8] if x.strip()):
                await conn.execute("INSERT INTO career_plan_tasks (area_id, plan_id, text, sort_order) VALUES ($1,$2,$3,$4)",
                                   aid, pid, t.strip()[:160], m)
