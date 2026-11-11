"""管理後台：匯入平台職缺（合作夥伴／ATS 公開 API）、待審清單、審核、ETL 執行紀錄（api.md §13）；會員方案與 token 額度。"""
import datetime as dt
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, HttpUrl

from app.core import db
from app.core.errors import bad_request, not_found
from app.core.security import CurrentUser, admin_user
from app.modules.admin.console import console_admin
from app.modules.jobs.service import search_text
from app.modules.membership import quota

router = APIRouter(prefix="/admin", tags=["admin"])


def norm_url(u: str) -> str:
    p = urlsplit(u)
    q = "&".join(x for x in p.query.split("&") if x and not re.match(r"(utm_|ref=|source=|fbclid|gclid)", x))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), q, ""))


class ImportJob(BaseModel):
    company_name: str = Field(max_length=120)
    title: str = Field(max_length=120)
    location_text: str | None = None
    city: str | None = None
    summary: str | None = Field(default=None, max_length=600)
    duties: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_site: str = Field(max_length=40)
    source_url: HttpUrl
    external_ref: str | None = Field(default=None, max_length=200)
    posted_at: dt.datetime | None = None
    work_mode: Literal["onsite", "hybrid", "remote"] | None = None
    employment_type: str | None = None
    salary_text: str | None = None
    is_foreign: bool = False
    work_language: Literal["zh", "en"] | None = None
    category_code: str | None = None
    seniority: Literal["junior", "mid", "senior", "lead"] | None = None


class ReviewIn(BaseModel):
    review_status: Literal["published", "rejected", "closed"]
    category_code: str | None = None
    seniority: Literal["junior", "mid", "senior", "lead"] | None = None
    note: str | None = None


@router.post("/jobs/import")
async def import_jobs(items: list[ImportJob], _: CurrentUser = Depends(admin_user)):
    inserted = updated = 0
    async with db.transaction() as conn:
        for j in items:
            cat = await conn.fetchval("SELECT id FROM job_categories WHERE code = $1", j.category_code) if j.category_code else None
            url = str(j.source_url)
            ref = j.external_ref or norm_url(url)
            r = await conn.fetchrow(
                """
                INSERT INTO job_posts (source, external_ref, company_name, title, city, location_text, summary, duties, requirements,
                       tags, source_site, source_url, source_url_norm, posted_at, work_mode, employment_type, salary_text,
                       is_foreign, work_language, category_id, seniority, fetched_at, last_verified_at, search_text, review_status)
                VALUES ('catalog',$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20, now(), now(), $21, 'published')
                ON CONFLICT (source_site, external_ref) WHERE external_ref IS NOT NULL DO UPDATE SET
                       company_name = EXCLUDED.company_name, title = EXCLUDED.title, city = EXCLUDED.city,
                       location_text = EXCLUDED.location_text, summary = EXCLUDED.summary, duties = EXCLUDED.duties,
                       requirements = EXCLUDED.requirements, tags = EXCLUDED.tags, source_url = EXCLUDED.source_url,
                       work_mode = EXCLUDED.work_mode, salary_text = EXCLUDED.salary_text, category_id = EXCLUDED.category_id,
                       seniority = EXCLUDED.seniority, last_verified_at = now(), search_text = EXCLUDED.search_text, status = 'active'
                RETURNING (xmax = 0) AS inserted
                """, ref, j.company_name, j.title, j.city, j.location_text, j.summary, j.duties, j.requirements, j.tags,
                j.source_site, url, norm_url(url), j.posted_at, j.work_mode, j.employment_type, j.salary_text, j.is_foreign,
                j.work_language, cat, j.seniority, search_text(j.company_name, j.title, j.tags))
            inserted += r["inserted"]
            updated += not r["inserted"]
    return {"inserted": inserted, "updated": updated}


@router.get("/jobs/review-queue")
async def review_queue(_: CurrentUser = Depends(admin_user)):
    async with db.connection() as conn:
        rs = await conn.fetch(
            """SELECT j.id, j.title, j.company_name, j.source_url, c.code, j.ai_confidence, j.review_reasons, j.fetched_at
                 FROM job_posts j LEFT JOIN job_categories c ON c.id = j.category_id
                WHERE j.review_status = 'pending_review' ORDER BY j.fetched_at LIMIT 100""")
    return {"items": [{"id": str(r["id"]), "title": r["title"], "company_name": r["company_name"], "source_url": r["source_url"],
                       "category": {"code": r["code"]}, "ai_confidence": float(r["ai_confidence"]) if r["ai_confidence"] is not None else None,
                       "review_reasons": r["review_reasons"], "fetched_at": r["fetched_at"]} for r in rs], "next_cursor": None}


@router.patch("/jobs/{job_id}/review")
async def review(job_id: str, body: ReviewIn, _: CurrentUser = Depends(admin_user)):
    async with db.transaction() as conn:
        cat = await conn.fetchval("SELECT id FROM job_categories WHERE code = $1", body.category_code) if body.category_code else None
        status = "closed" if body.review_status == "closed" else "active"
        rs = "published" if body.review_status == "closed" else body.review_status
        r = await conn.fetchval(
            """UPDATE job_posts SET review_status = $2, status = $3, category_id = COALESCE($4, category_id),
                      seniority = COALESCE($5, seniority) WHERE id = $1::uuid AND source = 'catalog' RETURNING id""",
            job_id, rs, status, cat, body.seniority)
    if not r:
        raise not_found()
    return {"id": job_id, "review_status": rs, "status": status}


@router.get("/job-ingest-runs")
async def ingest_runs(_: CurrentUser = Depends(admin_user)):
    async with db.connection() as conn:
        rs = await conn.fetch("SELECT * FROM job_ingest_runs ORDER BY started_at DESC LIMIT 50")
    return {"items": [dict(r) | {"id": str(r["id"])} for r in rs]}


# ---------- 會員方案與 token 額度（管理後台：名單內的 Google 帳號＋後台帳密，見 console.py）----------
class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    monthly_token_limit: int | None = Field(default=None, ge=0)        # None = 不限
    monthly_interview_limit: int | None = Field(default=None, ge=0)    # None = 不限
    hard_limit_ratio: float = Field(default=1.2, ge=1, le=5)
    price_twd: int = Field(default=0, ge=0)
    is_active: bool = True
    sort_order: int = 0


class MembershipIn(BaseModel):
    plan: str | None = Field(default=None, max_length=40)
    plan_expires_at: dt.datetime | None = None
    token_limit_override: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=300)


def _plan_out(r) -> dict:
    return {"code": r["code"], "name": r["name"], "monthly_token_limit": r["monthly_token_limit"],
            "monthly_interview_limit": r["monthly_interview_limit"], "hard_limit_ratio": float(r["hard_limit_ratio"]),
            "price_twd": r["price_twd"], "is_active": r["is_active"], "sort_order": r["sort_order"]}


@router.get("/plans")
async def list_plans(_: CurrentUser = Depends(console_admin)):
    async with db.connection() as conn:
        rs = await conn.fetch(
            """SELECT p.*, (SELECT count(*) FROM users u WHERE u.plan = p.code AND u.status <> 'deleted') AS members
                 FROM plans p ORDER BY sort_order, code""")
    return {"items": [_plan_out(r) | {"members": r["members"]} for r in rs]}


@router.put("/plans/{code}")
async def put_plan(code: str, body: PlanIn, _: CurrentUser = Depends(console_admin)):
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", code):
        raise bad_request("方案代碼只能用小寫英數與底線", {"code": "pattern"})
    async with db.transaction() as conn:
        r = await conn.fetchrow(
            """INSERT INTO plans (code, name, monthly_token_limit, monthly_interview_limit, hard_limit_ratio, price_twd,
                                  is_active, sort_order)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, monthly_token_limit = EXCLUDED.monthly_token_limit,
                      monthly_interview_limit = EXCLUDED.monthly_interview_limit, hard_limit_ratio = EXCLUDED.hard_limit_ratio,
                      price_twd = EXCLUDED.price_twd, is_active = EXCLUDED.is_active, sort_order = EXCLUDED.sort_order
               RETURNING *""", code, body.name, body.monthly_token_limit, body.monthly_interview_limit,
            body.hard_limit_ratio, body.price_twd, body.is_active, body.sort_order)
    return _plan_out(r)


@router.get("/users")
async def list_users(q: str | None = None, plan: str | None = None, over_quota: bool = False, limit: int = 50,
                     _: CurrentUser = Depends(console_admin)):
    """會員清單＋本月 token 用量。over_quota=true 只列出已達上限的人。"""
    async with db.connection() as conn:
        rs = await conn.fetch(
            """
            SELECT u.id, u.email, u.display_name, u.plan, u.role, u.status, u.plan_expires_at, u.token_limit_override,
                   u.created_at, u.last_login_at,
                   COALESCE(u.token_limit_override, p.monthly_token_limit) AS token_limit,
                   COALESCE(t.total_tokens, 0) AS tokens_used, COALESCE(t.call_count, 0) AS calls
              FROM users u
              JOIN plans p ON p.code = CASE WHEN u.plan_expires_at <= now() THEN 'free' ELSE u.plan END
              LEFT JOIN user_token_usage t ON t.user_id = u.id AND t.period_start = date_trunc('month', now())::date
             WHERE u.status <> 'deleted'
               AND ($1::text IS NULL OR u.email ILIKE '%' || $1 || '%' OR u.display_name ILIKE '%' || $1 || '%')
               AND ($2::text IS NULL OR u.plan = $2)
               AND (NOT $3 OR (COALESCE(u.token_limit_override, p.monthly_token_limit) IS NOT NULL
                               AND COALESCE(t.total_tokens, 0) >= COALESCE(u.token_limit_override, p.monthly_token_limit)))
             ORDER BY tokens_used DESC, u.created_at DESC
             LIMIT $4
            """, q, plan, over_quota, max(1, min(limit, 200)))
    return {"items": [dict(r) | {"id": str(r["id"])} for r in rs]}


@router.get("/users/{user_id}/usage")
async def user_usage(user_id: UUID, months: int = 6, _: CurrentUser = Depends(console_admin)):
    """單一會員：目前額度、近幾個月用量、本月各功能（purpose）用量、方案異動紀錄。"""
    async with db.connection() as conn:
        qt = await quota.status(conn, user_id)
        if not qt:
            raise not_found("找不到這個使用者")
        history = await conn.fetch(
            """SELECT period_start, input_tokens, output_tokens, cached_tokens, total_tokens, call_count
                 FROM user_token_usage WHERE user_id = $1 ORDER BY period_start DESC LIMIT $2""",
            user_id, max(1, min(months, 24)))
        by_purpose = await conn.fetch(
            """SELECT purpose, count(*) AS calls, COALESCE(sum(input_tokens), 0) AS input_tokens,
                      COALESCE(sum(output_tokens), 0) AS output_tokens
                 FROM llm_calls WHERE user_id = $1 AND status = 'ok' AND created_at >= date_trunc('month', now())
                GROUP BY purpose ORDER BY sum(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) DESC""", user_id)
        changes = await conn.fetch(
            "SELECT * FROM user_plan_changes WHERE user_id = $1 ORDER BY created_at DESC LIMIT 20", user_id)
    return {"membership": qt.out() | {"raw_plan": qt.raw_plan, "token_limit_overridden": qt.overridden,
                                      "hard_limit": qt.hard_limit},
            "history": [dict(r) for r in history],
            "this_month_by_purpose": [dict(r) for r in by_purpose],
            "plan_changes": [dict(r) | {"id": str(r["id"]), "user_id": str(r["user_id"]),
                                        "changed_by": str(r["changed_by"]) if r["changed_by"] else None} for r in changes]}


@router.patch("/users/{user_id}/membership")
async def set_membership(user_id: UUID, body: MembershipIn, admin: CurrentUser = Depends(console_admin)):
    """改方案／到期日／個人 token 上限。只會改有送出的欄位；送 null 代表清除（不到期、不覆寫）。"""
    sent = body.model_fields_set
    if not sent - {"reason"}:
        raise bad_request("沒有要變更的欄位")
    async with db.transaction() as conn:
        u = await conn.fetchrow("SELECT plan, plan_expires_at, token_limit_override FROM users WHERE id = $1 FOR UPDATE",
                                user_id)
        if not u:
            raise not_found("找不到這個使用者")
        plan = body.plan if "plan" in sent and body.plan else u["plan"]
        if plan != u["plan"] and not await conn.fetchval("SELECT 1 FROM plans WHERE code = $1 AND is_active", plan):
            raise bad_request("沒有這個方案或方案已停用", {"plan": "invalid"})
        expires = body.plan_expires_at if "plan_expires_at" in sent else u["plan_expires_at"]
        override = body.token_limit_override if "token_limit_override" in sent else u["token_limit_override"]
        await conn.execute(
            "UPDATE users SET plan = $2, plan_expires_at = $3, token_limit_override = $4 WHERE id = $1",
            user_id, plan, expires, override)
        await conn.execute(
            """INSERT INTO user_plan_changes (user_id, from_plan, to_plan, token_limit_override, plan_expires_at, changed_by, reason)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""", user_id, u["plan"], plan, override, expires, admin.id, body.reason)
        qt = await quota.status(conn, user_id)
    return qt.out() | {"raw_plan": qt.raw_plan, "token_limit_overridden": qt.overridden}
