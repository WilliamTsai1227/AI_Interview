"""求職準備（原「職涯規劃」）：使用者選一份履歷＋1–5 個目標職缺 → 能力差距 → 準備計畫（大項目／小項目）。

比對哪些職缺由使用者每次自己選：一次全抓會把不同類型職缺的要求混在一起，缺口失焦。
不使用平台職缺或網路搜尋（品質不穩，見 spec/features/career-plan.md）。
"""
import asyncio
import json
from uuid import UUID

import structlog

from app.ai.agents import career as ca
from app.core import db, redis
from app.core.config import settings
from app.core.errors import AppError, not_found
from app.modules.career import github
from app.modules.resumes import service as resumes
from app.workers.queue import enqueue, tx

log = structlog.get_logger()
MAX_TARGETS = 5             # 一次最多比對幾個職缺：太多不同類型的職缺會讓缺口失焦
REPO_CACHE_SEC = 7 * 86400
LAST_READ_SEC = 30 * 86400   # GitHub 限流時改用最近一次讀到的結果
READ_LINK_SEC = 200          # 每個連結（下載＋審閱）最多等多久，超過就略過，不拖垮整份分析
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
async def start(user_id: UUID, target_ids: list[UUID]) -> dict:
    target_ids = list(dict.fromkeys(target_ids))
    if not 1 <= len(target_ids) <= MAX_TARGETS:
        raise AppError(400, "VALIDATION_ERROR", f"請選 1–{MAX_TARGETS} 個要比對的目標職缺", {"fields": {"target_ids": "count"}})
    await redis.rate_limit("career_plan", str(user_id))
    async with tx() as conn:
        rows = await conn.fetch(
            """SELECT t.id, j.id AS job_id, j.parse_status FROM target_jobs t JOIN job_posts j ON j.id = t.job_post_id
                WHERE t.id = ANY($1::uuid[]) AND t.user_id = $2 AND t.status = 'active' AND j.deleted_at IS NULL""",
            target_ids, user_id)
        if len(rows) != len(target_ids):
            raise not_found("找不到部分目標職缺，請重新整理後再選")
        if any(r["parse_status"] in ("pending", "parsing") for r in rows):
            raise AppError(409, "RESOURCE_NOT_READY", "有職缺還在整理內容，完成後再開始分析")
        by_id = {r["id"]: r["job_id"] for r in rows}
        job_ids = [by_id[t] for t in target_ids]
        prof = await conn.fetchrow("SELECT * FROM career_profiles WHERE user_id = $1", user_id)
        p = _profile_out(prof)
        running = await conn.fetchval(
            "SELECT id FROM career_plans WHERE user_id = $1 AND status IN ('pending','running')", user_id)
        if running:
            return {"id": str(running), "status": "running"}
        # 履歷必填（自我介紹選填）：沒有履歷就無法判斷使用者會什麼，缺口分析沒有意義
        res = (await conn.fetchrow("SELECT id, parse_status FROM resumes WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
                                   UUID(p["resume_id"]), user_id) if p["resume_id"] else None) \
            or await conn.fetchrow("SELECT id, parse_status FROM resumes WHERE user_id = $1 AND is_primary AND deleted_at IS NULL", user_id)
        if not res:
            raise AppError(400, "VALIDATION_ERROR", "請先上傳履歷", {"fields": {"resume_id": "required"}})
        if res["parse_status"] in ("pending", "parsing"):
            raise AppError(409, "RESOURCE_NOT_READY", "履歷還在解析，完成後再開始分析")
        if res["parse_status"] != "parsed":
            raise AppError(400, "VALIDATION_ERROR", "這份履歷沒有解析成功，請換一份或重新上傳", {"fields": {"resume_id": "unparsed"}})
        rid = res["id"]
        pid = await conn.fetchval(
            "INSERT INTO career_plans (user_id, resume_id, profile_snapshot, target_job_ids) VALUES ($1,$2,$3,$4) RETURNING id",
            user_id, rid, {k: v for k, v in p.items() if k != "updated_at"}, job_ids)
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
    resume = await conn.fetchval("SELECT filename FROM resumes WHERE id = $1", p["resume_id"]) if p["resume_id"] else None
    chosen = await conn.fetch(
        """SELECT j.company_name, j.title FROM unnest($1::uuid[]) WITH ORDINALITY AS x(id, n)
             JOIN job_posts j ON j.id = x.id ORDER BY x.n""", p["target_job_ids"]) if p["target_job_ids"] else jobs_rs
    by_area: dict = {}
    for t in tasks:
        by_area.setdefault(t["area_id"], []).append({"id": str(t["id"]), "text": t["text"], "is_checked": t["is_checked"]})
    return {
        "id": str(p["id"]), "status": p["status"], "created_at": p["created_at"], "finished_at": p["finished_at"],
        "summary": p["summary"], "readiness_score": p["readiness_score"], "strengths": list(p["strengths"] or []),
        "gaps": p["gaps"] or [], "has_resume": p["resume_id"] is not None,
        "error": p["error"], "profile": p["profile_snapshot"], "stage": p["stage"], "portfolio": p["portfolio_evidence"] or [],
        # 這次比對了什麼：分析中、失敗時也看得到（舊分析沒有存選擇，用結果裡的職缺）
        "compare": {"resume": resume, "jobs": [{"company_name": r["company_name"], "title": r["title"]} for r in chosen]},
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
            """SELECT p.id, p.status, p.readiness_score, p.summary, p.created_at, jsonb_array_length(p.gaps) AS gap_count,
                      r.filename AS resume,
                      COALESCE(NULLIF(p.target_job_ids, '{}'), ARRAY(SELECT job_post_id FROM career_plan_jobs WHERE plan_id = p.id)) AS job_ids
                 FROM career_plans p LEFT JOIN resumes r ON r.id = p.resume_id
                WHERE p.user_id = $1 ORDER BY p.created_at DESC LIMIT 30""", user_id)
        names = dict(await conn.fetch("SELECT id, company_name FROM job_posts WHERE id = ANY($1::uuid[])",
                                      list({j for r in rs for j in r["job_ids"]}))) if rs else {}
    return [{"id": str(r["id"]), "status": r["status"], "readiness_score": r["readiness_score"], "summary": r["summary"],
             "gap_count": r["gap_count"], "created_at": r["created_at"], "resume": r["resume"],
             "companies": [names[j] for j in r["job_ids"] if j in names]} for r in rs]


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


async def _read_link(url: str, user_id: UUID) -> dict:
    """讀一個作品集連結並審閱。repo 的審閱結果依 commit 快取 7 天：程式碼沒變就不重新花錢分析。"""
    target = github.parse(url)
    if not target:
        return {"url": url, "status": "skipped", "error": "目前只會讀取 GitHub 連結"}
    owner, repo = target
    try:
        if not repo:
            return {"url": url, "status": "ok", "kind": "profile", "profile": await github.read_profile(owner)}
        r = await github.read_repo(owner, repo)
    except github.ReadError as e:
        if e.rate_limited and repo and (last := await _last_read(owner, repo)):
            return {**last, "url": url, "stale": True}
        return {"url": url, "status": "failed", "error": str(e)}
    except Exception as e:
        log.warning("portfolio_read_failed", url=url, error=repr(e))
        return {"url": url, "status": "failed", "error": "連不到 GitHub，請稍後再試"}
    m = r["meta"]
    if not r["files"] and not r["readme"]:
        return {"url": url, "status": "failed", "error": "這個 repo 沒有可以讀的程式碼", "kind": "repo", "repo": m, "files_read": []}
    key = f"{redis.P}career_repo:{ca.PV_REPO}:{m['full_name'].lower()}:{m['sha']}:{settings.portfolio_repo_chars}"
    analysis = None
    try:
        cached = await redis.client().get(key)
        analysis = json.loads(cached) if cached else None
    except Exception:
        pass
    if analysis is None:
        try:
            analysis = (await ca.analyze_repo(r, user_id=user_id)).model_dump()
        except Exception as e:
            return {"url": url, "status": "failed", "error": f"讀到了程式碼，但 Ava 審閱失敗：{str(e)[:80]}", "kind": "repo", "repo": m,
                    "files_read": [f["path"] for f in r["files"]]}
        try:
            await redis.client().set(key, json.dumps(analysis, ensure_ascii=False), ex=REPO_CACHE_SEC)
        except Exception:
            pass
    out = {"url": url, "status": "ok", "kind": "repo", "repo": {**m, "languages": r["languages"]},
           "files_read": [f["path"] for f in r["files"]], "readme_read": bool(r["readme"]), "commits_read": len(r["commits"]),
           "analysis": analysis}
    try:
        await redis.client().set(_last_key(owner, repo), json.dumps(out, ensure_ascii=False), ex=LAST_READ_SEC)
    except Exception:
        pass
    return out


def _last_key(owner: str, repo: str) -> str:
    return f"{redis.P}career_repo_last:{owner.lower()}/{repo.lower()}"


async def _last_read(owner: str, repo: str) -> dict | None:
    try:
        v = await redis.client().get(_last_key(owner, repo))
        return json.loads(v) if v else None
    except Exception:
        return None


async def read_portfolio(links: list[str], user_id: UUID) -> list[dict]:
    """同時讀所有連結；GitHub repo 最多深入讀 PORTFOLIO_MAX_REPOS 個，其餘標成略過。"""
    async def skipped(u: str) -> dict:
        return {"url": u, "status": "skipped", "error": f"一次最多讀 {settings.portfolio_max_repos} 個 repo"}
    out, n = [], 0
    for u in links:
        t = github.parse(u)
        n += bool(t and t[1])
        out.append(skipped(u) if t and t[1] and n > settings.portfolio_max_repos else _bounded(u, user_id))
    return list(await asyncio.gather(*out))


async def _bounded(url: str, user_id: UUID) -> dict:
    try:
        return await asyncio.wait_for(_read_link(url, user_id), READ_LINK_SEC)
    except asyncio.TimeoutError:
        return {"url": url, "status": "failed", "error": "這個 repo 太大，讀取逾時"}


async def handle_build(payload: dict) -> None:
    pid = UUID(payload["plan_id"])
    async with db.connection() as conn:
        p = await conn.fetchrow(
            "UPDATE career_plans SET status = 'running', stage = 'portfolio' WHERE id = $1 AND status IN ('pending','running') RETURNING *", pid)
        if not p:
            return
        profile = p["profile_snapshot"]
        r = await conn.fetchrow("SELECT * FROM resumes WHERE id = $1 AND deleted_at IS NULL", p["resume_id"]) if p["resume_id"] else None
        resume = resumes.snapshot(r)
        # 新分析用使用者選的職缺（依選擇順序）；舊分析沒有存選擇，沿用當時的做法抓最近的目標職缺
        targets = await conn.fetch(
            """SELECT j.* FROM unnest($1::uuid[]) WITH ORDINALITY AS x(id, n) JOIN job_posts j ON j.id = x.id
                WHERE j.deleted_at IS NULL ORDER BY x.n""", p["target_job_ids"]) if p["target_job_ids"] \
            else await _target_jobs(conn, p["user_id"])
    uid = p["user_id"]
    cands = [(t["id"], _job_dict(t)) for t in targets]
    portfolio = await read_portfolio(profile.get("portfolio_links") or [], uid)
    async with db.connection() as conn:
        await conn.execute("UPDATE career_plans SET portfolio_evidence = $2, stage = 'plan' WHERE id = $1", pid, portfolio)
    try:
        out = await ca.build_plan(profile=profile, resume=resume, jobs=[c[1] for c in cands], portfolio=portfolio, user_id=uid)
    except Exception as e:
        async with db.connection() as conn:
            await conn.execute("UPDATE career_plans SET status = 'failed', stage = NULL, error = $2, finished_at = now() WHERE id = $1", pid, str(e)[:300])
        return
    fits = {f.index: f for f in out.job_fits if 0 <= f.index < len(cands)}
    async with db.transaction() as conn:
        await conn.execute(
            """UPDATE career_plans SET status = 'ready', summary = $2, readiness_score = $3, strengths = $4, gaps = $5,
                      prompt_version = $6, stage = NULL, error = NULL, finished_at = now()
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
