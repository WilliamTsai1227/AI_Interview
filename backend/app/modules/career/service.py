"""職涯差距分析：個人檔案 → 找職缺（平台＋網路搜尋）→ 能力差距 → 準備計畫（大項目／小項目）。"""
from urllib.parse import urlsplit
from uuid import UUID

import structlog

from app.ai.agents import career as ca
from app.core import db, redis
from app.core.config import settings
from app.core.errors import AppError, not_found
from app.modules.career import urlcheck
from app.modules.jobs import service as jobs
from app.modules.resumes import service as resumes
from app.workers.queue import enqueue, tx

log = structlog.get_logger()
MAX_CATALOG, MAX_WEB = 8, 10
SEARCH_TRIES = 2          # 失敗（或檢查後沒有有效職缺）自動重新搜尋一次
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
        "gaps": p["gaps"] or [], "has_resume": p["resume_id"] is not None, "web_search_used": p["web_search_used"],
        "search": {"status": p["search_status"], "attempts": p["search_attempts"], "retries": max(0, p["search_attempts"] - 1),
                   "error": p["search_error"], "found": p["web_found"], "dropped": p["web_dropped"]},
        "error": p["error"], "profile": p["profile_snapshot"],
        "jobs": [{"id": str(r["job_post_id"]), "found_by": r["found_by"], "company_name": r["company_name"], "title": r["title"],
                  "city": r["city"], "salary_text": r["salary_text"], "source_url": r["source_url"],
                  "source_site": r["source_site"], "work_mode": r["work_mode"], "match_score": r["match_score"],
                  "reason": r["reason"], "gaps": list(r["gaps"] or []), "url_check": r["url_check"],
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
async def _catalog_candidates(conn: db.Connection, profile: dict) -> list[db.Record]:
    kws = [k.strip() for k in (profile["roles"] + profile["industries"]) if k.strip()]
    args: list = []

    def arg(v) -> str:
        args.append(v)
        return f"${len(args)}"
    where = ["j.source = 'catalog'", "j.status = 'active'", "j.review_status = 'published'", "j.deleted_at IS NULL"]
    score = "0"
    if kws:
        pats = arg([f"%{k}%" for k in kws])
        score = f"(CASE WHEN j.search_text ILIKE ANY({pats}) THEN 2 ELSE 0 END)"
    if profile["locations"]:
        where.append(f"(j.city = ANY({arg(profile['locations'])}) OR j.work_mode = 'remote')")
    if profile["work_modes"]:
        where.append(f"(j.work_mode IS NULL OR j.work_mode = ANY({arg(profile['work_modes'])}))")
    if profile["salary_min"]:
        monthly = profile["salary_min"] / (12 if profile["salary_period"] == "year" else 1)
        where.append(f"(j.salary_max IS NULL OR j.salary_max / (CASE j.salary_period WHEN 'year' THEN 12.0 ELSE 1.0 END) >= {arg(monthly)})")
    return await conn.fetch(
        f"SELECT j.* FROM job_posts j WHERE {' AND '.join(where)} ORDER BY {score} DESC, j.posted_at DESC NULLS LAST LIMIT {MAX_CATALOG}", *args)


def _norm_url(url: str) -> str:
    u = urlsplit(url.strip())
    return f"{u.scheme}://{u.netloc.lower()}{u.path.rstrip('/')}"


async def _save_web_job(conn: db.Connection, user_id: UUID, f: ca.FoundJob) -> UUID | None:
    if not f.source_url.startswith(("http://", "https://")) or not f.title.strip():
        return None
    norm = _norm_url(f.source_url)
    ex = await conn.fetchrow("SELECT id, source, owner_user_id FROM job_posts WHERE source_url_norm = $1 AND deleted_at IS NULL", norm)
    if ex and (ex["source"] == "catalog" or ex["owner_user_id"] == user_id):
        return ex["id"]
    tags = [t[:30] for t in f.tags[:8]]
    return await conn.fetchval(
        """INSERT INTO job_posts (source, owner_user_id, company_name, title, city, location_text, work_mode, salary_text,
                  summary, duties, requirements, tags, source_site, source_url, source_url_norm, fetched_at,
                  last_verified_at, search_text, parse_status, status)
           VALUES ('user',$1,$2,$3,NULLIF($4,''),NULLIF($4,''),$5,NULLIF($6,''),$7,$8,$9,$10,$11,$12,$13,now(),now(),$14,'parsed','active')
           RETURNING id""",
        user_id, f.company_name[:120] or "未知公司", f.title[:120], f.city[:20], f.work_mode if f.work_mode in ("onsite", "hybrid", "remote") else None,
        f.salary_text[:80], f.summary[:600], f.duties[:8], f.requirements[:8], tags, (f.source_site or "web")[:30].lower(),
        f.source_url[:1000], None if ex else norm, jobs.search_text(f.company_name, f.title, tags))


def _job_dict(r) -> dict:
    return {"company_name": r["company_name"], "title": r["title"], "salary_text": r["salary_text"], "city": r["city"],
            "requirements": list(r["requirements"] or []), "tags": list(r["tags"] or [])}


async def _search_web(pid: UUID, profile: dict, resume: dict | None, uid: UUID):
    """網路搜尋（強制搜尋、限定台灣）＋網址檢查；失敗或檢查後沒有有效職缺就重新搜尋一次。
    回傳（有效職缺, {網址: ok|unverified}, 搜尋紀錄）。搜尋失敗不影響整份分析，只用平台職缺。"""
    search = {"status": "skipped", "attempts": 0, "error": None, "found": 0, "dropped": 0}
    if not settings.ai_enabled:
        search["error"] = "開發模式（沒有 OpenAI 金鑰）不會網路搜尋"
        return [], {}, search
    for attempt in range(1, SEARCH_TRIES + 1):
        search["attempts"] = attempt
        async with db.connection() as conn:       # 讓前端在搜尋中就能顯示「第幾次搜尋」
            await conn.execute("UPDATE career_plans SET search_status = 'running', search_attempts = $2 WHERE id = $1", pid, attempt)
        try:
            found = await ca.search_jobs(profile=profile, resume=resume, count=MAX_WEB, user_id=uid)
            checks = await urlcheck.check_urls([f.source_url for f in found])
            valid = [f for f in found if checks.get(f.source_url)][:MAX_WEB]
            search.update(found=len(found), dropped=len(found) - len(valid))
            if valid:
                search.update(status="ok", error=None)
                return valid, checks, search
            search.update(status="failed", error="搜尋到的職缺網址都無法開啟" if found else "沒有找到符合條件的職缺")
        except Exception as e:
            search.update(status="failed", error=_search_error(e))
            log.warning("career_search_failed", plan_id=str(pid), attempt=attempt, error=repr(e))
    return [], {}, search


def _search_error(e: Exception) -> str:
    msg = str(e)
    if "沒有執行網路搜尋" in msg:
        return "模型沒有執行網路搜尋"
    if "Timeout" in type(e).__name__ or "timeout" in msg.lower():
        return "網路搜尋逾時"
    if "model" in msg.lower() and ("not found" in msg.lower() or "does not exist" in msg.lower()):
        return "搜尋模型不存在或沒有權限（請檢查 LLM_MODEL_SEARCH）"
    return "網路搜尋失敗：" + msg[:120]


async def handle_build(payload: dict) -> None:
    pid = UUID(payload["plan_id"])
    async with db.connection() as conn:
        p = await conn.fetchrow("UPDATE career_plans SET status = 'running' WHERE id = $1 AND status IN ('pending','running') RETURNING *", pid)
        if not p:
            return
        profile = p["profile_snapshot"]
        r = await conn.fetchrow("SELECT * FROM resumes WHERE id = $1 AND deleted_at IS NULL", p["resume_id"]) if p["resume_id"] else None
        resume = resumes.snapshot(r)
        catalog = await _catalog_candidates(conn, profile)
    uid = p["user_id"]
    web, checks, search = await _search_web(pid, profile, resume, uid)
    try:
        async with db.transaction() as conn:
            cands: list[tuple[UUID, str, dict]] = [(c["id"], "catalog", _job_dict(c)) for c in catalog]
            url_checks: dict = {}
            seen = {c[0] for c in cands}
            for f in web:
                jid = await _save_web_job(conn, uid, f)
                if jid and jid not in seen:
                    seen.add(jid)
                    cands.append((jid, "web", {"company_name": f.company_name, "title": f.title, "salary_text": f.salary_text,
                                              "city": f.city, "requirements": f.requirements, "tags": f.tags}))
                    url_checks[jid] = checks.get(f.source_url)
        out = await ca.build_plan(profile=profile, resume=resume, jobs=[c[2] for c in cands], user_id=uid)
    except Exception as e:
        async with db.connection() as conn:
            await conn.execute("UPDATE career_plans SET status = 'failed', error = $2, finished_at = now() WHERE id = $1", pid, str(e)[:300])
        return
    fits = {f.index: f for f in out.job_fits if 0 <= f.index < len(cands)}
    async with db.transaction() as conn:
        await conn.execute(
            """UPDATE career_plans SET status = 'ready', summary = $2, readiness_score = $3, strengths = $4, gaps = $5,
                      web_search_used = $6, prompt_version = $7, error = NULL, finished_at = now(),
                      search_status = $8, search_attempts = $9, search_error = $10, web_found = $11, web_dropped = $12
                WHERE id = $1""",
            pid, out.summary[:300], max(0, min(100, out.readiness_score)), [s[:80] for s in out.strengths[:5]],
            [{"skill": g.skill[:40], "severity": g.severity if g.severity in PRIORITY_RANK else "medium", "detail": g.detail[:160]}
             for g in out.gaps[:8]], bool(web), ca.PV_PLAN, search["status"], search["attempts"], search["error"],
            search["found"], search["dropped"])
        for i, (jid, found_by, _) in enumerate(cands):
            f = fits.get(i)
            await conn.execute(
                "INSERT INTO career_plan_jobs (plan_id, job_post_id, found_by, match_score, reason, gaps, url_check) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                pid, jid, found_by, max(0, min(100, f.score)) if f else None, f.reason[:160] if f else None,
                [g[:60] for g in f.gaps[:3]] if f else [], url_checks.get(jid))
        for n, a in enumerate(sorted(out.areas[:6], key=lambda x: PRIORITY_RANK.get(x.priority, 1))):
            aid = await conn.fetchval(
                "INSERT INTO career_plan_areas (plan_id, title, why, priority, sort_order) VALUES ($1,$2,$3,$4,$5) RETURNING id",
                pid, a.title[:60], a.why[:160], a.priority if a.priority in PRIORITY_RANK else "medium", n)
            for m, t in enumerate(x for x in a.tasks[:8] if x.strip()):
                await conn.execute("INSERT INTO career_plan_tasks (area_id, plan_id, text, sort_order) VALUES ($1,$2,$3,$4)",
                                   aid, pid, t.strip()[:160], m)
